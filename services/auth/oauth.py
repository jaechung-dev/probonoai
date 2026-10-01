"""
OAuth 2.0 authorization-code (+ PKCE) provider for the ChatGPT Custom GPT Action.

Flow
  1. GPT sends the user to   GET  /oauth/authorize?...      → redirects to the
     frontend consent page ``{FRONTEND_URL}/oauth/consent`` (TODO: frontend).
  2. The consent page (user is logged in with the normal web session) calls
     POST /oauth/authorize/approve with their web Bearer token → returns the
     redirect URL carrying a single-use, 60-second authorization code.
  3. OpenAI's servers call   POST /oauth/token  (code + client secret + PKCE
     verifier) and receive a 15-minute read-only access token plus a rotating
     refresh token.  Re-using an already-rotated refresh token revokes the whole
     grant and alerts the user.
  4. Users see / revoke grants via GET/DELETE /oauth/grants.
"""
from __future__ import annotations

import base64
import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlencode

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel

from services.auth.tokens import (
    AUD_GPT, GPT_ALLOWED_SCOPES, authenticate, rate_limit_user, request_ip,
    require_web, security_alert, ip_allowed, make_access_token,
)
from services.core.db import get_db
from services.core.settings import settings

router = APIRouter(prefix="/oauth", tags=["oauth"])

CODE_TTL_SECONDS = 60
REUSE_GRACE_SECONDS = 10          # tolerate a retried/racing refresh call
DEFAULT_SCOPES = ["cases:read", "conversations:read", "search", "ask"]


def _h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _redirect_uris() -> list[str]:
    return [u.strip() for u in settings.GPT_OAUTH_REDIRECT_URIS.split(",") if u.strip()]


def _same(a: str, b: str) -> bool:
    return secrets.compare_digest(a.encode(), b.encode())


def _validate_client(client_id: str, redirect_uri: str) -> None:
    if not settings.GPT_OAUTH_CLIENT_ID or not _same(client_id or "", settings.GPT_OAUTH_CLIENT_ID):
        raise HTTPException(400, "invalid_client")
    if redirect_uri not in _redirect_uris():        # exact match only
        raise HTTPException(400, "invalid_redirect_uri")


def _parse_scopes(scope: str | None) -> list[str]:
    wanted = (scope or "").replace(",", " ").split() or DEFAULT_SCOPES
    bad = [s for s in wanted if s not in GPT_ALLOWED_SCOPES]
    if bad:
        raise HTTPException(400, "invalid_scope")
    return sorted(set(wanted))


def _oauth_error(code: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": code}, status_code=status, headers={"Cache-Control": "no-store"})


# ── 1. Authorization endpoint → frontend consent page ─────────────────────────


@router.get("/authorize")
def authorize(
    response_type: str = Query(...), client_id: str = Query(...),
    redirect_uri: str = Query(...), scope: str = Query(default=""),
    state: str = Query(default=""), code_challenge: str = Query(default=""),
    code_challenge_method: str = Query(default="S256"),
):
    if response_type != "code":
        raise HTTPException(400, "unsupported_response_type")
    _validate_client(client_id, redirect_uri)
    _parse_scopes(scope)
    if settings.GPT_REQUIRE_PKCE and (not code_challenge or code_challenge_method != "S256"):
        raise HTTPException(400, "PKCE (S256) required")
    q = urlencode({
        "client_id": client_id, "redirect_uri": redirect_uri, "scope": scope,
        "state": state, "code_challenge": code_challenge,
        "code_challenge_method": code_challenge_method,
    })
    return RedirectResponse(f"{settings.FRONTEND_URL.rstrip('/')}/oauth/consent?{q}", status_code=302)


class ApproveRequest(BaseModel):
    client_id: str
    redirect_uri: str
    scope: str = ""
    state: str = ""
    code_challenge: str = ""
    code_challenge_method: str = "S256"
    approve: bool = True


@router.post("/authorize/approve")
def authorize_approve(req: ApproveRequest, authorization: str = Header(default=None)):
    """Called by the consent page with the user's normal web session."""
    claims = require_web(authorization)
    if not claims.get("email_verified"):
        raise HTTPException(403, "Verify your email before connecting apps")
    _validate_client(req.client_id, req.redirect_uri)
    scopes = _parse_scopes(req.scope)
    rate_limit_user(claims["sub"], "oauth_approve", 10, 60)

    sep = "&" if "?" in req.redirect_uri else "?"
    if not req.approve:
        return {"redirect_to": f"{req.redirect_uri}{sep}{urlencode({'error': 'access_denied', 'state': req.state})}"}
    if settings.GPT_REQUIRE_PKCE and (not req.code_challenge or req.code_challenge_method != "S256"):
        raise HTTPException(400, "PKCE (S256) required")

    code = secrets.token_urlsafe(32)
    with get_db() as conn:
        conn.cursor().execute(
            "INSERT INTO oauth_auth_codes (code_hash,user_id,client_id,redirect_uri,scopes,code_challenge,expires_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (_h(code), claims["sub"], req.client_id, req.redirect_uri, scopes,
             req.code_challenge or None,
             datetime.now(timezone.utc) + timedelta(seconds=CODE_TTL_SECONDS)),
        )
    security_alert("gpt_grant_approved", claims["sub"], claims.get("email"), {"scopes": scopes})
    return {"redirect_to": f"{req.redirect_uri}{sep}{urlencode({'code': code, 'state': req.state})}"}


# ── 2. Token endpoint ─────────────────────────────────────────────────────────


def _client_credentials(request: Request, form: dict[str, str]) -> tuple[str, str]:
    auth = request.headers.get("authorization", "")
    if auth.startswith("Basic "):
        try:
            cid, _, sec = base64.b64decode(auth[6:]).decode().partition(":")
            return cid, sec
        except Exception:
            return "", ""
    return form.get("client_id", ""), form.get("client_secret", "")


def _issue_tokens(user: dict, grant_id: str, scopes: list[str], conn) -> dict:
    raw_refresh = secrets.token_urlsafe(48)
    conn.cursor().execute(
        "INSERT INTO oauth_refresh_tokens (family_id,token_hash,expires_at) VALUES (%s,%s,%s)",
        (grant_id, _h(raw_refresh),
         datetime.now(timezone.utc) + timedelta(days=settings.GPT_REFRESH_TOKEN_DAYS)),
    )
    ttl = timedelta(minutes=settings.GPT_ACCESS_TOKEN_MINUTES)
    access = make_access_token(
        sub=user["id"], email=user["email"], name=user["name"], role=user["role"],
        scopes=scopes, aud=AUD_GPT, ttl=ttl, email_verified=user["email_verified"],
        grant_id=grant_id,
    )
    return {
        "access_token": access, "token_type": "Bearer",
        "expires_in": int(ttl.total_seconds()),
        "refresh_token": raw_refresh, "scope": " ".join(scopes),
    }


def _load_user(cur, user_id: str) -> dict | None:
    cur.execute("SELECT id,email,name,role,email_verified FROM users WHERE id=%s", (user_id,))
    r = cur.fetchone()
    if not r:
        return None
    return {"id": str(r[0]), "email": r[1], "name": r[2], "role": r[3], "email_verified": bool(r[4])}


@router.post("/token")
async def token(request: Request):
    ip = request_ip.get()
    # Only OpenAI's servers should ever call this (fail closed until configured).
    if settings.GPT_ENFORCE_IP and not ip_allowed(ip, settings.GPT_ALLOWED_CIDRS):
        security_alert("oauth_token_wrong_ip", None, detail={"ip": ip})
        return _oauth_error("access_denied", 403)
    rate_limit_user(f"ip:{ip}", "oauth_token", 30, 200)

    form = {k: v[0] for k, v in parse_qs((await request.body()).decode(), keep_blank_values=True).items()}
    cid, csec = _client_credentials(request, form)
    if (not settings.GPT_OAUTH_CLIENT_ID or not settings.GPT_OAUTH_CLIENT_SECRET
            or not _same(cid, settings.GPT_OAUTH_CLIENT_ID)
            or not _same(csec, settings.GPT_OAUTH_CLIENT_SECRET)):
        security_alert("oauth_bad_client_auth", None, detail={"ip": ip})
        return _oauth_error("invalid_client", 401)

    grant_type = form.get("grant_type", "")
    if grant_type == "authorization_code":
        return _grant_code(form, cid)
    if grant_type == "refresh_token":
        return _grant_refresh(form, cid)
    return _oauth_error("unsupported_grant_type")


def _ok(body: dict) -> JSONResponse:
    return JSONResponse(body, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})


def _grant_code(form: dict[str, str], client_id: str):
    code, verifier, redirect_uri = form.get("code", ""), form.get("code_verifier", ""), form.get("redirect_uri", "")
    if not code:
        return _oauth_error("invalid_request")
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(  # single use: consumed even if later checks fail
            "DELETE FROM oauth_auth_codes WHERE code_hash=%s "
            "RETURNING user_id,client_id,redirect_uri,scopes,code_challenge,expires_at",
            (_h(code),),
        )
        row = cur.fetchone()
        if not row:
            return _oauth_error("invalid_grant")
        user_id, c_client, c_redirect, scopes, challenge, expires_at = row
        if expires_at < datetime.now(timezone.utc) or c_client != client_id or c_redirect != redirect_uri:
            return _oauth_error("invalid_grant")
        if challenge:
            digest = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
            if not verifier or not _same(digest, challenge):
                security_alert("oauth_pkce_failed", str(user_id))
                return _oauth_error("invalid_grant")
        elif settings.GPT_REQUIRE_PKCE:
            return _oauth_error("invalid_grant")
        user = _load_user(cur, str(user_id))
        if not user:
            return _oauth_error("invalid_grant")
        scopes = [s for s in scopes if s in GPT_ALLOWED_SCOPES]
        grant_id = str(uuid.uuid4())
        cur.execute(
            "INSERT INTO oauth_grants (id,user_id,client_id,scopes) VALUES (%s,%s,%s,%s)",
            (grant_id, user["id"], client_id, scopes),
        )
        body = _issue_tokens(user, grant_id, scopes, conn)
    return _ok(body)


def _revoke_grant(cur, grant_id: str) -> None:
    cur.execute("UPDATE oauth_grants SET revoked_at=now() WHERE id=%s AND revoked_at IS NULL", (grant_id,))


def _grant_refresh(form: dict[str, str], client_id: str):
    raw = form.get("refresh_token", "")
    if not raw:
        return _oauth_error("invalid_request")
    alert = None
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT rt.id,rt.family_id,rt.used_at,rt.expires_at,g.user_id,g.scopes,g.revoked_at,g.client_id "
            "FROM oauth_refresh_tokens rt JOIN oauth_grants g ON g.id=rt.family_id "
            "WHERE rt.token_hash=%s",
            (_h(raw),),
        )
        row = cur.fetchone()
        if not row:
            return _oauth_error("invalid_grant")
        rt_id, family, used_at, expires_at, user_id, scopes, revoked_at, g_client = row
        now = datetime.now(timezone.utc)
        if revoked_at or g_client != client_id or expires_at < now:
            return _oauth_error("invalid_grant")
        if used_at is not None:
            if (now - used_at).total_seconds() <= REUSE_GRACE_SECONDS:
                return _oauth_error("invalid_grant")        # benign retry/race
            _revoke_grant(cur, str(family))                   # theft signal: kill the family
            user = _load_user(cur, str(user_id))
            alert = (str(user_id), user["email"] if user else None, str(family))
        else:
            cur.execute(
                "UPDATE oauth_refresh_tokens SET used_at=now() WHERE id=%s AND used_at IS NULL", (rt_id,)
            )
            if cur.rowcount != 1:
                return _oauth_error("invalid_grant")
            user = _load_user(cur, str(user_id))
            if not user:
                return _oauth_error("invalid_grant")
            body = _issue_tokens(user, str(family), [s for s in scopes if s in GPT_ALLOWED_SCOPES], conn)
            cur.execute("DELETE FROM oauth_refresh_tokens WHERE family_id=%s AND expires_at<now()", (family,))
            return _ok(body)
    uid, email, fam = alert
    security_alert("oauth_refresh_token_reuse", uid, email, {"grant": fam}, notify_user=True)
    return _oauth_error("invalid_grant")


@router.post("/revoke")
async def revoke(request: Request):
    form = {k: v[0] for k, v in parse_qs((await request.body()).decode(), keep_blank_values=True).items()}
    cid, csec = _client_credentials(request, form)
    if (not settings.GPT_OAUTH_CLIENT_SECRET or not _same(cid, settings.GPT_OAUTH_CLIENT_ID)
            or not _same(csec, settings.GPT_OAUTH_CLIENT_SECRET)):
        return _oauth_error("invalid_client", 401)
    raw = form.get("token", "")
    if raw:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT family_id FROM oauth_refresh_tokens WHERE token_hash=%s", (_h(raw),))
            r = cur.fetchone()
            if r:
                _revoke_grant(cur, str(r[0]))
    return _ok({})


# ── 3. User-facing management of connected apps ───────────────────────────────


@router.get("/grants")
def list_grants(authorization: str = Header(default=None)):
    claims = require_web(authorization)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id,client_id,scopes,created_at,last_used_at FROM oauth_grants "
            "WHERE user_id=%s AND revoked_at IS NULL ORDER BY created_at DESC", (claims["sub"],))
        rows = cur.fetchall()
    return {"grants": [{
        "id": str(r[0]), "app": "ChatGPT" if r[1] == settings.GPT_OAUTH_CLIENT_ID else r[1],
        "scopes": r[2], "connected_at": r[3].isoformat() if r[3] else None,
        "last_used_at": r[4].isoformat() if r[4] else None,
    } for r in rows]}


@router.delete("/grants/{grant_id}")
def delete_grant(grant_id: str, authorization: str = Header(default=None)):
    claims = require_web(authorization)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE oauth_grants SET revoked_at=now() WHERE id=%s AND user_id=%s AND revoked_at IS NULL",
            (grant_id, claims["sub"]))
        if cur.rowcount == 0:
            raise HTTPException(404, "Grant not found")
    return {"ok": True}
