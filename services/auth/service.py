"""
Auth service — all /auth/* route handlers extracted from main.py.

Exposes a FastAPI APIRouter (``router``) with prefix="/auth".
Import and include this router in the BFF app:

    from services.auth.service import router as auth_router
    app.include_router(auth_router)
"""
import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
import psycopg2
from fastapi import APIRouter, Header, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from jose import jwt
from pydantic import BaseModel

from services.core.settings import settings
from services.core.db import get_db
from services.core.cache import get_redis

# ── Config aliases (kept for backward-compat imports) ─────────────────────────

DSN        = settings.DATABASE_URL
JWT_SECRET = settings.JWT_SECRET
JWT_ALG    = settings.JWT_ALG

GOOGLE_CLIENT_ID     = settings.GOOGLE_CLIENT_ID
GOOGLE_CLIENT_SECRET = settings.GOOGLE_CLIENT_SECRET
FRONTEND_URL         = settings.FRONTEND_URL
BACKEND_URL          = settings.BACKEND_URL
APP_NAME             = settings.APP_NAME

SEED_USERS = {
    "demo":  {"password": settings.DEMO_PASSWORD,  "name": "Guest", "role": "user"},
    "admin": {"password": settings.ADMIN_PASSWORD, "name": "Admin", "role": "admin"},
}
SEED_IDS = {
    "demo":  "00000000-0000-0000-0000-000000000001",
    "admin": "00000000-0000-0000-0000-000000000002",
}
SCOPES = ["search", "ask", "chat", "timeline"]

# OAuth CSRF state is persisted in Postgres (see _store/_consume_oauth_state)
# so it survives across concurrent Lambda instances; Redis is used as a fast
# path when REDIS_URL is set.

# ── Pydantic models ────────────────────────────────────────────────────────────


class LoginRequest(BaseModel):
    username: str
    password: str


class RegisterRequest(BaseModel):
    name: str
    email: str
    password: str


class EmailRequest(BaseModel):
    email: str


class ResetPasswordRequest(BaseModel):
    token: str
    password: str


class OTPVerifyRequest(BaseModel):
    email: str
    code: str


class OAuthTokenRequest(BaseModel):
    api_key: str
    scopes: list[str] = ["search", "ask", "chat"]  # noqa: RUF012


class MCPTokenRequest(BaseModel):
    name: str = "My MCP Token"
    scopes: list[str] = ["search", "ask", "fetch", "collections"]  # noqa: RUF012
    expires_days: int = 365


# ── DB helpers ─────────────────────────────────────────────────────────────────

_db = get_db  # alias so all existing call sites (_db() as conn) work unchanged


def _h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _hash_password(password: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}{password}".encode()).hexdigest()


def _verify_password(password: str, salt: str, stored_hash: str) -> bool:
    return _hash_password(password, salt) == stored_hash


def _db_user(email: str) -> dict | None:
    with _db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id,email,name,password_hash,salt,role,provider,email_verified "
            "FROM users WHERE email=%s",
            (email,),
        )
        row = cur.fetchone()
    if not row:
        return None
    return dict(zip(
        ["id", "email", "name", "password_hash", "salt", "role", "provider", "email_verified"],
        row,
    ))


def _store_oauth_state(state: str) -> None:
    """Persist the OAuth CSRF state in Postgres so any Lambda instance can verify
    it on callback (an in-memory dict is lost across concurrent instances)."""
    with _db() as conn:
        conn.cursor().execute(
            "INSERT INTO oauth_states (state, expires_at) "
            "VALUES (%s, now() + interval '5 minutes') ON CONFLICT (state) DO NOTHING",
            (state,),
        )


def _consume_oauth_state(state: str) -> bool:
    """Single-use + expiry check, atomic via DELETE ... RETURNING."""
    with _db() as conn:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM oauth_states WHERE state = %s AND expires_at > now() RETURNING state",
            (state,),
        )
        return cur.fetchone() is not None


def _log_access(user_id: str, email: str, method: str, request: Request | None) -> None:
    """Record a sign-in (who / when / from where) for site-usage auditing.
    Best-effort: never blocks or fails a login."""
    try:
        ip, ua = "", ""
        if request is not None:
            ip = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
            ua = (request.headers.get("user-agent") or "")[:400]
        with _db() as conn:
            conn.cursor().execute(
                "INSERT INTO access_logs (user_id, email, method, ip, user_agent) "
                "VALUES (%s, %s, %s, %s, %s)",
                (str(user_id), email, method, ip, ua),
            )
    except Exception:  # pragma: no cover — never block a login on logging
        pass


# ── JWT helpers ────────────────────────────────────────────────────────────────


def _make_jwt(
    user_id: str, name: str, role: str, scopes: list[str],
    hours: int = 1, email: str = "", email_verified: bool = True,
) -> str:
    return jwt.encode({
        "sub":            user_id,
        "email":          email,
        "name":           name,
        "role":           role,
        "scopes":         scopes,
        "email_verified": email_verified,
        "iss":            "probonoai.com.au",
        "aud":            "probonoai-api",
        "exp":            datetime.now(timezone.utc) + timedelta(hours=hours),
    }, JWT_SECRET, algorithm=JWT_ALG)


def _issue(
    user_id: str, email: str, name: str, role: str,
    email_verified: bool = True,
) -> dict:
    access = _make_jwt(user_id, name, role, SCOPES, hours=1, email=email, email_verified=email_verified)
    raw    = secrets.token_urlsafe(48)
    exp    = datetime.now(timezone.utc) + timedelta(days=30)
    with _db() as conn:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM refresh_tokens WHERE user_id=%s AND expires_at<NOW()", (user_id,)
        )
        cur.execute(
            "INSERT INTO refresh_tokens (user_id, token_hash, expires_at) VALUES (%s,%s,%s)",
            (user_id, _h(raw), exp),
        )
    return {
        "access_token":  access,
        "refresh_token": raw,
        "token_type":    "bearer",
        "expires_in":    3600,
    }


# ── Refresh-token cookie helpers ──────────────────────────────────────────────

_SECURE_COOKIE = "localhost" not in settings.BACKEND_URL
_REFRESH_MAX_AGE = 30 * 24 * 3600  # 30 days, matches _issue()


def _set_refresh_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key="iai_refresh",
        value=token,
        max_age=_REFRESH_MAX_AGE,
        httponly=True,
        secure=_SECURE_COOKIE,
        samesite="strict",
        path="/auth",  # only sent to /auth/* endpoints
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(key="iai_refresh", path="/auth", samesite="strict")


# ── Auth guard (shared with BFF) ───────────────────────────────────────────────


def require_auth(authorization: str | None) -> str:
    """Validate session JWT, return user_id UUID."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Bearer token required")
    try:
        p = jwt.decode(authorization[7:], JWT_SECRET, algorithms=[JWT_ALG], audience="probonoai-api")
    except Exception:
        raise HTTPException(401, "Invalid or expired token")
    sub = p.get("sub", "")
    if not sub:
        raise HTTPException(401, "Invalid token")
    return sub


# ── Email helpers ──────────────────────────────────────────────────────────────


def _send_email(to: str, subject: str, html: str, text: str):
    try:
        import boto3
        boto3.client("ses", region_name=settings.AWS_REGION_NAME).send_email(
            Source=settings.FROM_EMAIL,
            Destination={"ToAddresses": [to]},
            Message={
                "Subject": {"Data": subject, "Charset": "UTF-8"},
                "Body": {
                    "Html": {"Data": html, "Charset": "UTF-8"},
                    "Text": {"Data": text, "Charset": "UTF-8"},
                },
            },
        )
    except Exception as e:
        print(f"SES_ERROR {e}", flush=True)


def _email_otp(to: str, name: str, code: str):
    _send_email(
        to, f"Your {APP_NAME} verification code",
        f'<p>Hi {name},</p>'
        f'<p>Your email verification code is:</p>'
        f'<p style="font-size:40px;font-weight:700;letter-spacing:10px;color:#10b981;font-family:monospace">{code}</p>'
        f'<p style="color:#6b7280;font-size:13px">Expires in 15 minutes. If you didn\'t sign up, ignore this email.</p>',
        f"Your verification code: {code}  (expires in 15 minutes)",
    )


def _email_reset(to: str, name: str, token: str):
    link = f"{FRONTEND_URL}/reset-password/?token={token}"
    _send_email(
        to, "Reset your ProBono AI password",
        f'<p>Hi {name},</p><p>Click the link below to reset your password:</p>'
        f'<p><a href="{link}" style="background:#10b981;color:#fff;padding:12px 24px;border-radius:8px;text-decoration:none;font-weight:600;display:inline-block">Reset password</a></p>'
        f'<p style="color:#6b7280;font-size:13px">Link expires in 1 hour. If you didn\'t request this, ignore this email.</p>',
        f"Reset your password: {link}",
    )


# ── Seed demo/admin users on startup ──────────────────────────────────────────


def seed_db():
    try:
        with _db() as conn:
            cur = conn.cursor()
            for key, info in SEED_USERS.items():
                cur.execute(
                    "INSERT INTO users (id,email,name,role,provider,email_verified) "
                    "VALUES (%s,%s,%s,%s,'seed',true) ON CONFLICT (id) DO NOTHING",
                    (SEED_IDS[key], key, info["name"], info["role"]),
                )
    except Exception as e:
        print(f"SEED_ERROR {e}", flush=True)


# ── Rate limiting ─────────────────────────────────────────────────────────────


async def _rate_limit(key: str, max_attempts: int = 5, window: int = 900) -> None:
    """Increment attempt counter per key; raise 429 if over limit. No-op when Redis unavailable."""
    redis = await get_redis()
    if redis is None:
        return
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, window)
    if count > max_attempts:
        raise HTTPException(429, "Too many attempts. Please wait 15 minutes and try again.")


# ── Router ─────────────────────────────────────────────────────────────────────

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register")
def auth_register(req: RegisterRequest, response: Response):
    email = req.email.lower().strip()
    if not req.name.strip():
        raise HTTPException(422, "Name is required")
    if "@" not in email:
        raise HTTPException(422, "Invalid email address")
    if len(req.password) < 8:
        raise HTTPException(422, "Password must be at least 8 characters")

    with _db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id FROM users WHERE email=%s", (email,))
        if cur.fetchone():
            raise HTTPException(409, "An account with this email already exists")
        salt    = secrets.token_hex(16)
        user_id = str(uuid.uuid4())
        cur.execute(
            "INSERT INTO users (id,email,name,password_hash,salt,role,provider,email_verified) "
            "VALUES (%s,%s,%s,%s,%s,'user','local',false)",
            (user_id, email, req.name.strip(), _hash_password(req.password, salt), salt),
        )

    code = str(secrets.randbelow(900000) + 100000)
    vexp = datetime.now(timezone.utc) + timedelta(minutes=15)
    with _db() as conn:
        conn.cursor().execute(
            "INSERT INTO email_verifications (user_id,token_hash,expires_at) VALUES (%s,%s,%s)",
            (user_id, _h(code), vexp),
        )
    _email_otp(email, req.name.strip(), code)

    tokens = _issue(user_id, email, req.name.strip(), "user", email_verified=False)
    _set_refresh_cookie(response, tokens["refresh_token"])
    return {
        "access_token": tokens["access_token"],
        "token_type":   tokens["token_type"],
        "expires_in":   tokens["expires_in"],
        "user": {
            "username": email, "name": req.name.strip(),
            "role": "user", "email_verified": False,
        },
    }


@router.post("/login")
async def auth_login(req: LoginRequest, request: Request, response: Response):
    await _rate_limit(f"rate:login:{req.username.lower().strip()}")
    seed = SEED_USERS.get(req.username)
    if seed and seed["password"] == req.password:
        tokens = _issue(
            SEED_IDS[req.username], req.username, seed["name"], seed["role"], email_verified=True
        )
        _set_refresh_cookie(response, tokens["refresh_token"])
        _log_access(SEED_IDS[req.username], req.username, "demo", request)
        return {
            "access_token": tokens["access_token"],
            "token_type":   tokens["token_type"],
            "expires_in":   tokens["expires_in"],
            "user": {
                "username": req.username, "name": seed["name"],
                "role": seed["role"], "email_verified": True,
            },
        }

    email = req.username.lower().strip()
    user  = _db_user(email)
    if not user:
        raise HTTPException(401, "Invalid email or password")
    if user["provider"] == "google":
        raise HTTPException(401, "This account uses Google sign-in. Use 'Continue with Google'.")
    if not _verify_password(req.password, user["salt"] or "", user["password_hash"] or ""):
        raise HTTPException(401, "Invalid email or password")

    tokens = _issue(user["id"], email, user["name"], user["role"],
                    email_verified=user["email_verified"])
    _set_refresh_cookie(response, tokens["refresh_token"])
    _log_access(user["id"], email, "password", request)
    return {
        "access_token": tokens["access_token"],
        "token_type":   tokens["token_type"],
        "expires_in":   tokens["expires_in"],
        "user": {
            "username": email, "name": user["name"],
            "role": user["role"], "email_verified": user["email_verified"],
        },
    }


@router.post("/refresh")
def auth_refresh(request: Request, response: Response):
    raw = request.cookies.get("iai_refresh")
    if not raw:
        raise HTTPException(401, "Refresh token required")
    with _db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT rt.user_id,u.email,u.name,u.role,u.email_verified "
            "FROM refresh_tokens rt JOIN users u ON u.id=rt.user_id "
            "WHERE rt.token_hash=%s AND rt.expires_at>NOW()",
            (_h(raw),),
        )
        row = cur.fetchone()
        if not row:
            _clear_refresh_cookie(response)
            raise HTTPException(401, "Invalid or expired refresh token")
        user_id, email, name, role, ev = row
        cur.execute("DELETE FROM refresh_tokens WHERE token_hash=%s", (_h(raw),))

    tokens = _issue(user_id, email, name, role, email_verified=ev)
    _set_refresh_cookie(response, tokens["refresh_token"])
    return {
        "access_token": tokens["access_token"],
        "token_type":   tokens["token_type"],
        "expires_in":   tokens["expires_in"],
        "user": {"username": email, "name": name, "role": role, "email_verified": ev},
    }


@router.post("/logout")
def auth_logout(request: Request, response: Response):
    raw = request.cookies.get("iai_refresh")
    if raw:
        with _db() as conn:
            conn.cursor().execute(
                "DELETE FROM refresh_tokens WHERE token_hash=%s", (_h(raw),)
            )
    _clear_refresh_cookie(response)
    return {"ok": True}


@router.get("/me")
def auth_me(authorization: str = Header(default=None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Bearer token required")
    try:
        p = jwt.decode(authorization[7:], JWT_SECRET, algorithms=[JWT_ALG])
    except Exception:
        raise HTTPException(401, "Invalid or expired token")
    return {
        "username":       p.get("email"),
        "name":           p.get("name"),
        "role":           p.get("role"),
        "email_verified": p.get("email_verified", True),
        "scopes":         p.get("scopes", []),
    }


@router.get("/verify-email")
def auth_verify_email(token: str = Query(...)):
    h = _h(token)
    with _db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id,user_id FROM email_verifications "
            "WHERE token_hash=%s AND expires_at>NOW() AND used=false",
            (h,),
        )
        row = cur.fetchone()
        if not row:
            return RedirectResponse(f"{FRONTEND_URL}/login/?error=invalid_link")
        ev_id, user_id = row
        cur.execute("UPDATE email_verifications SET used=true WHERE id=%s", (ev_id,))
        cur.execute("UPDATE users SET email_verified=true WHERE id=%s", (user_id,))
    return RedirectResponse(f"{FRONTEND_URL}/login/?verified=1")


@router.post("/verify-otp")
async def auth_verify_otp(req: OTPVerifyRequest):
    email = req.email.lower().strip()
    await _rate_limit(f"rate:otp:{email}")
    user  = _db_user(email)
    if not user:
        raise HTTPException(400, "Invalid or expired code")
    code_hash = _h(req.code.strip())
    with _db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id FROM email_verifications "
            "WHERE user_id=%s AND token_hash=%s AND expires_at>NOW() AND used=false",
            (user["id"], code_hash),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(400, "Invalid or expired code")
        cur.execute("UPDATE email_verifications SET used=true WHERE id=%s", (row[0],))
        cur.execute("UPDATE users SET email_verified=true WHERE id=%s", (user["id"],))
    return {"ok": True}


@router.post("/resend-verification")
def auth_resend_verification(req: EmailRequest):
    email = req.email.lower().strip()
    user  = _db_user(email)
    if not user or user["email_verified"]:
        return {"ok": True}
    with _db() as conn:
        conn.cursor().execute(
            "UPDATE email_verifications SET used=true WHERE user_id=%s AND used=false",
            (user["id"],),
        )
    code = str(secrets.randbelow(900000) + 100000)
    vexp = datetime.now(timezone.utc) + timedelta(minutes=15)
    with _db() as conn:
        conn.cursor().execute(
            "INSERT INTO email_verifications (user_id,token_hash,expires_at) VALUES (%s,%s,%s)",
            (user["id"], _h(code), vexp),
        )
    _email_otp(email, user["name"], code)
    return {"ok": True}


@router.post("/forgot-password")
def auth_forgot_password(req: EmailRequest):
    email = req.email.lower().strip()
    user  = _db_user(email)
    if not user or user["provider"] == "google":
        return {"ok": True}
    with _db() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE password_resets SET used=true WHERE user_id=%s AND used=false", (user["id"],)
        )
        rtok = secrets.token_urlsafe(32)
        rexp = datetime.now(timezone.utc) + timedelta(hours=1)
        cur.execute(
            "INSERT INTO password_resets (user_id,token_hash,expires_at) VALUES (%s,%s,%s)",
            (user["id"], _h(rtok), rexp),
        )
    _email_reset(email, user["name"], rtok)
    return {"ok": True}


@router.post("/reset-password")
def auth_reset_password(req: ResetPasswordRequest):
    if len(req.password) < 8:
        raise HTTPException(422, "Password must be at least 8 characters")
    h = _h(req.token)
    with _db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id,user_id FROM password_resets "
            "WHERE token_hash=%s AND expires_at>NOW() AND used=false",
            (h,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(400, "Invalid or expired reset link")
        pr_id, user_id = row
        salt    = secrets.token_hex(16)
        pw_hash = _hash_password(req.password, salt)
        cur.execute("UPDATE password_resets SET used=true WHERE id=%s", (pr_id,))
        cur.execute(
            "UPDATE users SET password_hash=%s,salt=%s WHERE id=%s", (pw_hash, salt, user_id)
        )
        cur.execute("DELETE FROM refresh_tokens WHERE user_id=%s", (user_id,))
    return {"ok": True}


@router.post("/mcp/token")
def create_mcp_token(req: MCPTokenRequest, authorization: str = Header(default=None)):
    """Generate a long-lived MCP token. Requires a valid session JWT."""
    user_id  = require_auth(authorization)
    raw      = "mcp-" + secrets.token_urlsafe(32)
    exp      = datetime.now(timezone.utc) + timedelta(days=req.expires_days)
    token_id = str(uuid.uuid4())
    with _db() as conn:
        conn.cursor().execute(
            "INSERT INTO mcp_tokens (id,user_id,token_hash,name,scopes,expires_at) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (token_id, user_id, _h(raw), req.name.strip() or "My MCP Token", req.scopes, exp),
        )
    return {"token": raw, "id": token_id, "name": req.name, "expires_at": exp.isoformat()}


@router.get("/mcp/tokens")
def list_mcp_tokens(authorization: str = Header(default=None)):
    """List the caller's active MCP tokens (raw token is never returned)."""
    user_id = require_auth(authorization)
    with _db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id,name,scopes,expires_at,last_used_at,created_at "
            "FROM mcp_tokens WHERE user_id=%s AND expires_at>NOW() "
            "ORDER BY created_at DESC",
            (user_id,),
        )
        rows = cur.fetchall()
    return {"tokens": [
        {
            "id":           str(r[0]),
            "name":         r[1],
            "scopes":       r[2],
            "expires_at":   r[3].isoformat() if r[3] else None,
            "last_used_at": r[4].isoformat() if r[4] else None,
            "created_at":   r[5].isoformat() if r[5] else None,
        }
        for r in rows
    ]}


@router.delete("/mcp/token/{token_id}")
def revoke_mcp_token(token_id: str, authorization: str = Header(default=None)):
    """Revoke an MCP token by ID."""
    user_id = require_auth(authorization)
    with _db() as conn:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM mcp_tokens WHERE id=%s AND user_id=%s", (token_id, user_id)
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "Token not found")
    return {"ok": True}


@router.get("/google")
async def google_login():
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(501, "Google OAuth not configured")
    state = secrets.token_urlsafe(32)
    redis = await get_redis()
    if redis:
        await redis.setex(f"oauth_state:{state}", 300, "1")
    else:
        _store_oauth_state(state)
    params = urlencode({
        "client_id":     GOOGLE_CLIENT_ID,
        "redirect_uri":  f"{BACKEND_URL}/auth/google/callback",
        "response_type": "code",
        "scope":         "openid email profile",
        "access_type":   "offline",
        "state":         state,
        "prompt":        "select_account",
    })
    return RedirectResponse(f"https://accounts.google.com/o/oauth2/v2/auth?{params}")


@router.get("/google/callback")
async def google_callback(request: Request, code: str = Query(...), state: str = Query(...)):
    redis = await get_redis()
    if redis:
        valid = await redis.getdel(f"oauth_state:{state}")
    else:
        valid = _consume_oauth_state(state)
    if not valid:
        raise HTTPException(400, "Invalid or expired OAuth state")
    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code":          code,
                "client_id":     GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "redirect_uri":  f"{BACKEND_URL}/auth/google/callback",
                "grant_type":    "authorization_code",
            },
        )
        if token_resp.status_code != 200:
            raise HTTPException(400, "OAuth token exchange failed")
        userinfo = (await client.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {token_resp.json()['access_token']}"},
        )).json()

    email = userinfo.get("email", "").lower()
    name  = userinfo.get("name", email)
    if not email:
        raise HTTPException(400, "No email returned by Google")

    user = _db_user(email)
    if user:
        # Only link a Google login to an account that was itself created via
        # Google. Refuse to sign in over an existing password account with the
        # same email address (that would be account takeover).
        if user["provider"] != "google":
            return RedirectResponse(f"{FRONTEND_URL}/login?error=account_exists")
        user_id = user["id"]
    else:
        user_id = str(uuid.uuid4())
        with _db() as conn:
            conn.cursor().execute(
                "INSERT INTO users (id,email,name,role,provider,email_verified) "
                "VALUES (%s,%s,%s,'user','google',true)",
                (user_id, email, name),
            )

    tokens = _issue(user_id, email, name, user["role"] if user else "user", email_verified=True)
    _log_access(user_id, email, "google", request)
    resp = RedirectResponse(
        f"{FRONTEND_URL}/auth/callback?token={tokens['access_token']}",
        status_code=302,
    )
    _set_refresh_cookie(resp, tokens["refresh_token"])
    return resp
