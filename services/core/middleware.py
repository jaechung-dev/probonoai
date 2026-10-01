import time
from datetime import datetime, timezone

from jose import jwt
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from services.auth.tokens import client_ip_from_headers, request_ip
from services.core.settings import settings


class AccessLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start = time.time()
        # Trusted client IP (right-most-trusted XFF entry — the left side is
        # attacker-controlled). Exposed to auth checks via a contextvar.
        ip = client_ip_from_headers(
            request.headers.get("x-forwarded-for", ""),
            request.client.host if request.client else "-",
        )
        request_ip.set(ip)
        response = await call_next(request)
        ms = round((time.time() - start) * 1000)
        user = "-"
        auth = request.headers.get("authorization", "")
        if auth.startswith("Bearer ") and settings.JWT_SECRET:
            try:
                payload = jwt.decode(auth[7:], settings.JWT_SECRET, algorithms=[settings.JWT_ALG])
                user = payload.get("sub", "-")
            except Exception:
                pass
        print(
            f'ACCESS {datetime.now(timezone.utc).isoformat()} '
            f'ip={ip} method={request.method} path={request.url.path} '
            f'status={response.status_code} ms={ms} user={user}',
            flush=True,
        )
        return response
