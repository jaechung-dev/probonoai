"""
FastAPI dependency helpers shared across API route modules.

All token validation goes through services.auth.tokens.authenticate so scope,
audience, grant-revocation, IP and rate-limit rules are enforced in one place.
"""
from fastapi import HTTPException
from jose import jwt

from services.auth.tokens import AUD_WEB, authenticate
from services.core.settings import settings


def get_user_from_header(authorization: str | None) -> str:
    """Returns user_id UUID or 'anon'. Never raises — used for anonymous-friendly routes.

    Only first-party web tokens are honoured here; GPT/OAuth tokens are treated as
    anonymous (deny-by-default: they can reach only routes that name a scope).
    """
    if not authorization or not authorization.startswith("Bearer "):
        return "anon"
    try:
        claims = jwt.decode(
            authorization[7:], settings.JWT_SECRET,
            algorithms=[settings.JWT_ALG], audience=AUD_WEB,
        )
        return claims.get("sub", "anon")
    except Exception:
        return "anon"


def require_auth(authorization: str | None, scope: str | None = None) -> str:
    """Returns user_id UUID or raises 401/403/429.

    ``scope`` is the permission this route needs (e.g. "cases:read"). GPT tokens
    are rejected on any route that does not name an allowed scope.
    """
    claims = authenticate(authorization, scope)
    sub = claims.get("sub")
    if not sub:
        raise HTTPException(status_code=401, detail="Invalid token")
    return sub
