"""OAuth behaviour of the MCP server middleware (no DB / network)."""
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from services.mcp.tests.test_server import _import_server


def _ctx(scopes, uid="u1"):
    st = SimpleNamespace(user_id=uid, scopes=scopes)
    return SimpleNamespace(request_context=SimpleNamespace(request=SimpleNamespace(state=st)))


class TestScopeGuard(unittest.TestCase):
    def setUp(self):
        self.srv = _import_server()

    def test_oauth_scope_enforced(self):
        self.assertEqual(self.srv._require_scope(_ctx({"search"}), "search"), "u1")
        with self.assertRaises(PermissionError):
            self.srv._require_scope(_ctx({"search"}), "cases:read")

    def test_static_token_has_all_tools(self):
        self.assertEqual(self.srv._require_scope(_ctx(None), "cases:read"), "u1")

    def test_resource_metadata_url(self):
        with patch.object(self.srv.settings, "MCP_RESOURCE_URL", "https://api.probonoai.com.au/mcp"):
            self.assertEqual(self.srv._resource_metadata_url(),
                             "https://api.probonoai.com.au/.well-known/oauth-protected-resource/mcp")


class TestMiddleware(unittest.IsolatedAsyncioTestCase):
    async def _post(self, headers=None):
        import httpx
        from httpx import ASGITransport
        srv = _import_server()
        self.srv = srv
        async with httpx.AsyncClient(transport=ASGITransport(app=srv.app), base_url="http://testserver") as c:
            return await c.post("/mcp", content=b"{}", headers={"Content-Type": "application/json", **(headers or {})})

    async def test_unauthenticated_challenge_points_at_metadata(self):
        r = await self._post()
        self.assertEqual(r.status_code, 401)
        self.assertIn('resource_metadata="https://api.probonoai.com.au/.well-known/oauth-protected-resource/mcp"',
                      r.headers["www-authenticate"])

    async def test_valid_oauth_token_reaches_app(self):
        import httpx
        from httpx import ASGITransport
        srv = _import_server()
        with patch.object(srv, "authenticate_mcp", return_value={"sub": "u1", "scopes": ["search"]}):
            async with httpx.AsyncClient(transport=ASGITransport(app=srv.app), base_url="http://testserver") as c:
                r = await c.post("/mcp", content=b"{}", headers={"Authorization": "Bearer a.b.c"})
        self.assertEqual(r.status_code, 200)

    async def test_invalid_oauth_token_401_with_challenge(self):
        import httpx
        from httpx import ASGITransport
        srv = _import_server()
        with patch.object(srv, "authenticate_mcp", side_effect=HTTPException(401, "invalid_token")):
            async with httpx.AsyncClient(transport=ASGITransport(app=srv.app), base_url="http://testserver") as c:
                r = await c.post("/mcp", content=b"{}", headers={"Authorization": "Bearer a.b.c"})
        self.assertEqual(r.status_code, 401)
        self.assertIn('error="invalid_token"', r.headers["www-authenticate"])

    async def test_forbidden_ip_is_403_not_401(self):
        import httpx
        from httpx import ASGITransport
        srv = _import_server()
        with patch.object(srv, "authenticate_mcp", side_effect=HTTPException(403, "forbidden")):
            async with httpx.AsyncClient(transport=ASGITransport(app=srv.app), base_url="http://testserver") as c:
                r = await c.post("/mcp", content=b"{}", headers={"Authorization": "Bearer a.b.c"})
        self.assertEqual(r.status_code, 403)

    async def test_unknown_static_token_rejected(self):
        with patch("psycopg2.connect") as conn:
            cur = conn.return_value.cursor.return_value
            cur.fetchone.return_value = None
            r = await self._post({"Authorization": "Bearer mcp-nope"})
        self.assertEqual(r.status_code, 401)


if __name__ == "__main__":
    unittest.main()
