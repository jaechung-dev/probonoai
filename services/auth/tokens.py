"""
Shared token / authorisation helpers (no FastAPI router here — safe to import
from deps, the auth service and the OAuth router without cycles).

Two audiences exist:
  * ``probonoai-api`` — first-party web app tokens.
  * ``probonoai-gpt`` — ChatGPT Custom GPT Action tokens: short-lived (15 min),
    read-only scopes, bound to an OAuth grant that can be revoked instantly,
    only accepted from the configured OpenAI IP ranges, rate limited per user.
"""
from __future__ import annotations

import contextvars
import ipaddress
import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from jose import jwt

from services.core.db import get_db
from services.core.settings import settings

logger = logging.getLogger(__name__)

AUD_WEB = "probonoai-api"
AUD_GPT = "probonoai-gpt"
ISSUER = "probonoai.com.au"

LEGACY_SCOPES = ["search", "ask", "chat", "timeline"]
WEB_SCOPES = LEGACY_SCOPES + [
    "cases:read", "cases:write", "conversations:read", "conversations:write",
]
# The ONLY scopes a GPT token can ever carry. Read-only by design: a prompt-
# injected GPT must not be able to modify or delete anything.
GPT_ALLOWED_SCOPES = ["cases:read", "conversations:read", "search", "ask", "timeline"]

# Per-request context set by the access-log middleware so that sync auth
# helpers can see the caller IP without every route taking a Request.
request_ip: contextvars.ContextVar[str] = contextvars.ContextVar("request_ip", default="")


# ── Client IP ─────────────────────────────────────────────────────────────────


def client_ip_from_headers(xff: str, fallback: str = "") -> str:
    """Pick the viewer IP from X-Forwarded-For counting from the RIGHT.

    Entries to the left of the trusted proxies are client-controlled and are
    never used. ``TRUSTED_PROXY_HOPS`` = number of trusted proxies that appended
    their own address after the real viewer (0 → the right-most entry).
    """
    parts = [p.strip() for p in (xff or "").split(",") if p.strip()]
    idx = len(parts) - 1 - settings.TRUSTED_PROXY_HOPS
    if 0 <= idx < len(parts):
        return parts[idx]
    return fallback


def _cidrs(raw: str):
    out = []
    for c in (raw or "").split(","):
        c = c.strip()
        if not c:
            continue
        try:
            out.append(ipaddress.ip_network(c, strict=False))
        except ValueError:
            logger.error("Invalid CIDR in GPT_ALLOWED_CIDRS: %r", c)
    return out


def ip_allowed(ip: str, raw_cidrs: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in net for net in _cidrs(raw_cidrs))


# ── Security events / alerting ────────────────────────────────────────────────


def security_alert(kind: str, user_id: str | None = None, email: str | None = None,
                   detail: dict | None = None, notify_user: bool = False) -> None:
    """Log a greppable SECURITY_ALERT line, store it, optionally email the user.
    Best-effort — never raises into the request path."""
    detail = detail or {}
    ip = request_ip.get()
    print("SECURITY_ALERT " + json.dumps(
        {"kind": kind, "user": user_id, "ip": ip, **detail}, default=str), flush=True)
    try:
        with get_db() as conn:
            conn.cursor().execute(
                "INSERT INTO security_events (user_id, kind, ip, detail) VALUES (%s,%s,%s,%s)",
                (user_id, kind, ip, json.dumps(detail, default=str)),
            )
    except Exception:  # pragma: no cover
        pass
    try:
        from services.auth.service import _send_email  # lazy: avoids import cycle
        subject = "Security alert on your ProBono AI account"
        body = (f"We detected: {kind.replace('_', ' ')}. "
                "We have signed out the affected session as a precaution. "
                "If this wasn't you, reset your password and review Connected apps.")
        if notify_user and email:
            _send_email(email, subject, f"<p>{body}</p>", body)
        if settings.SECURITY_ALERT_EMAIL:
            _send_email(settings.SECURITY_ALERT_EMAIL, f"[ALERT] {kind}",
                        f"<pre>{json.dumps({'user': user_id, 'ip': ip, **detail}, default=str)}</pre>",
                        f"{kind} user={user_id} ip={ip} {detail}")
    except Exception:  # pragma: no cover
        pass


# ── Per-user rate limiting (Redis if configured, else per-instance memory) ────

_mem_lock = threading.Lock()
_mem: dict[str, tuple[int, float]] = {}
_sync_redis = None


def _redis_sync():
    global _sync_redis
    if _sync_redis is None and settings.REDIS_URL:
        try:
            import redis
            _sync_redis = redis.Redis.from_url(settings.REDIS_URL, socket_timeout=1)
        except Exception:  # pragma: no cover
            _sync_redis = False
    return _sync_redis or None


def _hit(key: str, window: int) -> int:
    r = _redis_sync()
    if r is not None:
        try:
            n = r.incr(key)
            if n == 1:
                r.expire(key, window)
            return int(n)
        except Exception:  # pragma: no cover
            pass
    now = time.time()
    with _mem_lock:
        n, exp = _mem.get(key, (0, 0.0))
        if now >= exp:
            n, exp = 0, now + window
        n += 1
        _mem[key] = (n, exp)
        if len(_mem) > 5000:
            for k in [k for k, (_, e) in _mem.items() if e < now]:
                _mem.pop(k, None)
        return n


def rate_limit_user(user_id: str, bucket: str, per_minute: int, per_hour: int) -> None:
    """Raise 429 and raise a SECURITY_ALERT when a user exceeds either window."""
    m = _hit(f"rl:{bucket}:{user_id}:m", 60)
    h = _hit(f"rl:{bucket}:{user_id}:h", 3600)
    if m > per_minute or h > per_hour:
        if m in (per_minute + 1,) or h in (per_hour + 1,):  # alert once per window
            security_alert("rate_limit_exceeded", user_id,
                           detail={"bucket": bucket, "per_min": m, "per_hour": h})
        raise HTTPException(429, "Rate limit exceeded")


# ── Token issue ───────────────────────────────────────────────────────────────


def make_access_token(*, sub: str, email: str, name: str, role: str, scopes: list[str],
                      aud: str, ttl: timedelta, email_verified: bool = True,
                      grant_id: str | None = None) -> str:
    claims = {
        "sub": sub, "email": email, "name": name, "role": role,
        "scopes": scopes, "email_verified": email_verified,
        "iss": ISSUER, "aud": aud,
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + ttl,
    }
    if grant_id:
        claims["gid"] = grant_id
    return jwt.encode(claims, settings.JWT_SECRET, algorithm=settings.JWT_ALG)


# ── Verification (single choke-point used by every route) ─────────────────────


def _grant_active(grant_id: str) -> bool:
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM oauth_grants WHERE id=%s AND revoked_at IS NULL", (grant_id,))
            ok = cur.fetchone() is not None
            if ok:
                cur.execute(
                    "UPDATE oauth_grants SET last_used_at=now() "
                    "WHERE id=%s AND (last_used_at IS NULL OR last_used_at < now() - interval '60 seconds')",
                    (grant_id,),
                )
            return ok
    except Exception:
        return False  # fail closed


def authenticate(authorization: str | None, scope: str | None = None) -> dict:
    """Validate a Bearer token and return its claims. Raises 401/403/429.

    * aud=probonoai-api (web): if the token carries scopes, ``scope`` must be among
      them (legacy 4-scope tokens are treated as full web tokens for their 1 h life).
    * aud=probonoai-gpt: deny by default — the route MUST name a scope, the scope
      must be in GPT_ALLOWED_SCOPES and in the token, the OAuth grant must be
      unrevoked, the caller IP must be in the OpenAI allowlist, and the per-user
      rate limit must not be exceeded.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Not authenticated")
    raw = authorization[7:]
    claims = None
    for aud in (AUD_WEB, AUD_GPT):
        try:
            claims = jwt.decode(raw, settings.JWT_SECRET, algorithms=[settings.JWT_ALG], audience=aud)
            break
        except Exception:
            continue
    if not claims or not claims.get("sub"):
        raise HTTPException(401, "Invalid token")

    scopes = claims.get("scopes")
    if claims.get("aud") == AUD_GPT:
        if not scope or scope not in GPT_ALLOWED_SCOPES or scope not in (scopes or []):
            security_alert("gpt_scope_denied", claims["sub"], detail={"wanted": scope})
            raise HTTPException(403, "Insufficient scope")
        if settings.GPT_ENFORCE_IP and not ip_allowed(request_ip.get(), settings.GPT_ALLOWED_CIDRS):
            security_alert("gpt_token_wrong_ip", claims["sub"], detail={"ip": request_ip.get()})
            raise HTTPException(403, "Forbidden")
        gid = claims.get("gid")
        if not gid or not _grant_active(gid):
            raise HTTPException(401, "Grant revoked or invalid")
        rate_limit_user(claims["sub"], "gpt", settings.GPT_RATE_PER_MINUTE, settings.GPT_RATE_PER_HOUR)
    elif scope and scopes is not None:
        effective = WEB_SCOPES if set(scopes) == set(LEGACY_SCOPES) else scopes
        if scope not in effective:
            raise HTTPException(403, "Insufficient scope")
    return claims


def require_web(authorization: str | None) -> dict:
    """First-party session only (used by account/consent/token-management routes)."""
    claims = authenticate(authorization)
    if claims.get("aud") != AUD_WEB:
        raise HTTPException(403, "Web session required")
    return claims
