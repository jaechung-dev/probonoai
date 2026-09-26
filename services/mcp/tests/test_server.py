"""
Smoke tests for services/mcp/server.py

No real DB or network calls.  The `mcp` package, psycopg2, and requests are
mocked so the module can be imported without installing the MCP SDK or having
credentials.
"""
import sys
import types
import unittest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Stub the `mcp` package before any import so server.py doesn't need it installed
# ---------------------------------------------------------------------------

def _install_mcp_stub():
    """Insert a minimal mcp stub into sys.modules."""
    if "mcp" in sys.modules:
        return  # already installed (real or stub)

    # Build the stub hierarchy: mcp, mcp.server, mcp.server.fastmcp
    mcp_mod = types.ModuleType("mcp")
    mcp_server = types.ModuleType("mcp.server")
    mcp_fastmcp = types.ModuleType("mcp.server.fastmcp")

    # FastMCP stub: its streamable_http_app() returns a simple ASGI callable
    class _FakeFastMCP:
        def __init__(self, *args, **kwargs):
            pass

        def tool(self):
            def decorator(fn):
                return fn
            return decorator

        def streamable_http_app(self):
            async def _asgi(scope, receive, send):
                # Minimal ASGI response so Starlette middleware doesn't error
                if scope.get("type") == "http":
                    await send({
                        "type": "http.response.start",
                        "status": 200,
                        "headers": [[b"content-type", b"application/json"]],
                    })
                    await send({"type": "http.response.body", "body": b"{}", "more_body": False})
            return _asgi

    class _FakeContext:
        pass

    mcp_fastmcp.FastMCP = _FakeFastMCP
    mcp_fastmcp.Context = _FakeContext

    mcp_mod.server = mcp_server
    mcp_server.fastmcp = mcp_fastmcp

    sys.modules["mcp"] = mcp_mod
    sys.modules["mcp.server"] = mcp_server
    sys.modules["mcp.server.fastmcp"] = mcp_fastmcp


_install_mcp_stub()

# ---------------------------------------------------------------------------
# Patches applied before server.py is imported
# ---------------------------------------------------------------------------

_PSYCOPG2_PATCH  = patch("psycopg2.connect", side_effect=Exception("no db in tests"))
_REQUESTS_PATCH  = patch("requests.post",    return_value=MagicMock(status_code=200, json=lambda: {}))
_REQUESTS_G_PATCH = patch("requests.get",   return_value=MagicMock(status_code=200, json=lambda: {"model": "test"}))


def _import_server():
    with _PSYCOPG2_PATCH, _REQUESTS_PATCH, _REQUESTS_G_PATCH:
        import importlib
        import services.mcp.server as srv
        importlib.reload(srv)
        return srv


# ---------------------------------------------------------------------------
# Test: handler and app exist
# ---------------------------------------------------------------------------

class TestServerObjects(unittest.TestCase):
    def setUp(self):
        self.srv = _import_server()

    def test_handler_exists(self):
        from mangum import Mangum
        self.assertIsNotNone(self.srv.handler)
        self.assertIsInstance(self.srv.handler, Mangum)

    def test_app_is_asgi_callable(self):
        # An ASGI app must be callable (takes scope, receive, send)
        self.assertTrue(callable(self.srv.app))


# ---------------------------------------------------------------------------
# Test: unauthenticated request to /mcp returns 401
# ---------------------------------------------------------------------------

class TestMCPAuth(unittest.IsolatedAsyncioTestCase):
    async def test_unauthenticated_returns_401(self):
        import httpx
        from httpx import ASGITransport

        srv = _import_server()
        # Ensure psycopg2 stays mocked during the request so MCPAuthMiddleware
        # doesn't try to connect when validating the (missing) token.
        with patch("psycopg2.connect", side_effect=Exception("no db")):
            transport = ASGITransport(app=srv.app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                response = await client.post(
                    "/mcp",
                    content=b"{}",
                    headers={"Content-Type": "application/json"},
                )

        self.assertEqual(response.status_code, 401)

    async def test_unauthenticated_response_has_error_key(self):
        import httpx
        from httpx import ASGITransport

        srv = _import_server()
        with patch("psycopg2.connect", side_effect=Exception("no db")):
            transport = ASGITransport(app=srv.app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                response = await client.post(
                    "/mcp",
                    content=b"{}",
                    headers={"Content-Type": "application/json"},
                )

        body = response.json()
        self.assertIn("error", body)

    async def test_root_path_is_public(self):
        """The root path '/' should bypass auth (defined in _PUBLIC)."""
        import httpx
        from httpx import ASGITransport

        srv = _import_server()
        with patch("psycopg2.connect", side_effect=Exception("no db")):
            transport = ASGITransport(app=srv.app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                response = await client.get("/")

        # Root is in _PUBLIC — should NOT return 401
        self.assertNotEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
