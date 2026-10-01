"""
Tests for the GPT-Action hardening work (no DB / network).

Covers: scope enforcement, audience separation, GPT IP allowlist, per-user rate
limit, bcrypt + legacy password verification, MCP token cap, OAuth PKCE and
refresh-token reuse detection (with a scripted fake DB cursor).
"""
import asyncio
import base64
import contextlib
import hashlib
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from urllib.parse import urlencode

from fastapi import HTTPException

from services.auth import oauth, tokens
from services.core.settings import settings


def _bearer(**kw):
    base = dict(sub="u1", email="a@b.c", name="A", role="user", email_verified=True)
    base.update(kw)
    return "Bearer " + tokens.make_access_token(**base)


def _gpt(scopes=("cases:read",), gid="g1", ttl=timedelta(minutes=15)):
    return _bearer(scopes=list(scopes), aud=tokens.AUD_GPT, ttl=ttl, grant_id=gid)


def _web(scopes=None):
    return _bearer(scopes=scopes or tokens.WEB_SCOPES, aud=tokens.AUD_WEB, ttl=timedelta(hours=1))


class _Settings:
    """Patch several settings at once."""
    def __init__(self, **kw):
        self.p = [patch.object(settings, k, v) for k, v in kw.items()]

    def __enter__(self):
        for p in self.p:
            p.start()

    def __exit__(self, *a):
        for p in self.p:
            p.stop()


GPT_CFG = dict(GPT_ENFORCE_IP=True, GPT_ALLOWED_CIDRS="203.0.113.0/24", GPT_RATE_PER_MINUTE=5,
               GPT_RATE_PER_HOUR=100, JWT_SECRET="x" * 40)


class TestScopeEnforcement(unittest.TestCase):
    def setUp(self):
        tokens._mem.clear()
        self.cfg = _Settings(**GPT_CFG)
        self.cfg.__enter__()
        self.t = tokens.request_ip.set("203.0.113.7")
        self.g = patch.object(tokens, "_grant_active", return_value=True)
        self.g.start()
        self.a = patch.object(tokens, "security_alert")
        self.alert = self.a.start()

    def tearDown(self):
        self.a.stop(); self.g.stop(); self.cfg.__exit__()
        tokens.request_ip.reset(self.t)

    def test_gpt_happy_path(self):
        c = tokens.authenticate(_gpt(), "cases:read")
        self.assertEqual(c["sub"], "u1")

    def test_gpt_route_without_scope_is_denied(self):
        with self.assertRaises(HTTPException) as e:
            tokens.authenticate(_gpt(), None)
        self.assertEqual(e.exception.status_code, 403)

    def test_gpt_cannot_use_write_scope_even_if_token_claims_it(self):
        with self.assertRaises(HTTPException) as e:
            tokens.authenticate(_gpt(scopes=("cases:write",)), "cases:write")
        self.assertEqual(e.exception.status_code, 403)

    def test_gpt_token_missing_scope(self):
        with self.assertRaises(HTTPException) as e:
            tokens.authenticate(_gpt(scopes=("search",)), "cases:read")
        self.assertEqual(e.exception.status_code, 403)

    def test_gpt_wrong_ip(self):
        tokens.request_ip.set("198.51.100.9")
        with self.assertRaises(HTTPException) as e:
            tokens.authenticate(_gpt(), "cases:read")
        self.assertEqual(e.exception.status_code, 403)
        self.assertTrue(self.alert.called)

    def test_gpt_fails_closed_when_no_cidrs_configured(self):
        with _Settings(GPT_ALLOWED_CIDRS=""):
            with self.assertRaises(HTTPException) as e:
                tokens.authenticate(_gpt(), "cases:read")
        self.assertEqual(e.exception.status_code, 403)

    def test_revoked_grant_is_rejected_immediately(self):
        with patch.object(tokens, "_grant_active", return_value=False):
            with self.assertRaises(HTTPException) as e:
                tokens.authenticate(_gpt(), "cases:read")
        self.assertEqual(e.exception.status_code, 401)

    def test_gpt_token_without_grant_id_rejected(self):
        with self.assertRaises(HTTPException):
            tokens.authenticate(_gpt(gid=None), "cases:read")

    def test_expired_gpt_token(self):
        with self.assertRaises(HTTPException) as e:
            tokens.authenticate(_gpt(ttl=timedelta(seconds=-5)), "cases:read")
        self.assertEqual(e.exception.status_code, 401)

    def test_rate_limit_per_user(self):
        for _ in range(5):
            tokens.authenticate(_gpt(), "cases:read")
        with self.assertRaises(HTTPException) as e:
            tokens.authenticate(_gpt(), "cases:read")
        self.assertEqual(e.exception.status_code, 429)
        self.assertTrue(any(c.args[0] == "rate_limit_exceeded" for c in self.alert.call_args_list))

    def test_web_token_scope_enforced(self):
        tokens.authenticate(_web(), "cases:read")
        with self.assertRaises(HTTPException) as e:
            tokens.authenticate(_web(scopes=["search"]), "cases:write")
        self.assertEqual(e.exception.status_code, 403)

    def test_legacy_web_token_still_works(self):
        tokens.authenticate(_web(scopes=tokens.LEGACY_SCOPES), "conversations:write")

    def test_gpt_token_rejected_by_require_web(self):
        with self.assertRaises(HTTPException) as e:
            tokens.require_web(_gpt())
        self.assertEqual(e.exception.status_code, 403)

    def test_token_signed_with_other_secret(self):
        bad = _gpt()
        with _Settings(JWT_SECRET="y" * 40):
            with self.assertRaises(HTTPException):
                tokens.authenticate(bad, "cases:read")

    def test_get_user_from_header_ignores_gpt_tokens(self):
        from services.api.deps import get_user_from_header
        self.assertEqual(get_user_from_header(_gpt()), "anon")
        self.assertEqual(get_user_from_header(_web()), "u1")


class TestDataMinimisation(unittest.TestCase):
    def _detail(self, aud):
        import asyncio as _a
        from services.api.routes import cases
        row = ("c1", {"name": "Jae", "address": "1 Street"}, {"type": "housing"},
               [{"name": "lease.pdf", "key": "s3://secret/key"}], datetime.now(timezone.utc), "u1")
        cur = FakeCursor([row])
        t = tokens.token_aud.set(aud)
        try:
            with patch.object(cases, "get_db", _db(cur)), patch.object(cases, "require_auth", return_value="u1"):
                return _a.run(cases.get_case_detail("c1", "Bearer x"))
        finally:
            tokens.token_aud.reset(t)

    def test_gpt_does_not_receive_personal_details_or_storage_keys(self):
        out = self._detail(tokens.AUD_GPT)
        self.assertNotIn("personal", out)
        self.assertEqual(out["files"], [{"name": "lease.pdf"}])

    def test_web_still_receives_full_case(self):
        out = self._detail(tokens.AUD_WEB)
        self.assertIn("personal", out)
        self.assertIn("key", out["files"][0])

    def test_authenticate_records_audience(self):
        tokens.authenticate(_web(), "cases:read")
        self.assertEqual(tokens.token_aud.get(), tokens.AUD_WEB)


class TestClientIp(unittest.TestCase):
    def test_rightmost_by_default(self):
        with _Settings(TRUSTED_PROXY_HOPS=0):
            self.assertEqual(tokens.client_ip_from_headers("6.6.6.6, 1.2.3.4"), "1.2.3.4")

    def test_hops(self):
        with _Settings(TRUSTED_PROXY_HOPS=1):
            self.assertEqual(tokens.client_ip_from_headers("6.6.6.6, 1.2.3.4, 10.0.0.1"), "1.2.3.4")

    def test_spoofed_left_entry_ignored(self):
        with _Settings(TRUSTED_PROXY_HOPS=0):
            self.assertNotEqual(tokens.client_ip_from_headers("203.0.113.5, 198.51.100.1"), "203.0.113.5")

    def test_ip_allowed(self):
        self.assertTrue(tokens.ip_allowed("203.0.113.9", "203.0.113.0/24, 2001:db8::/32"))
        self.assertFalse(tokens.ip_allowed("8.8.8.8", "203.0.113.0/24"))
        self.assertFalse(tokens.ip_allowed("not-an-ip", "203.0.113.0/24"))
        self.assertFalse(tokens.ip_allowed("203.0.113.9", ""))


class TestPasswords(unittest.TestCase):
    def setUp(self):
        import importlib
        with patch("psycopg2.connect", side_effect=Exception("no db")):
            import services.auth.service as svc
            self.svc = importlib.reload(svc)

    def test_bcrypt_roundtrip(self):
        h = self.svc._hash_password_bcrypt("correct horse")
        self.assertTrue(h.startswith("$2"))
        self.assertTrue(self.svc._verify_password("correct horse", "", h))
        self.assertFalse(self.svc._verify_password("wrong", "", h))

    def test_long_passwords_not_truncated(self):
        p1, p2 = "a" * 100 + "X", "a" * 100 + "Y"
        h = self.svc._hash_password_bcrypt(p1)
        self.assertTrue(self.svc._verify_password(p1, "", h))
        self.assertFalse(self.svc._verify_password(p2, "", h))

    def test_legacy_sha256_still_verifies(self):
        h = self.svc._hash_password("pw12345678", "salt")
        self.assertTrue(self.svc._verify_password("pw12345678", "salt", h))
        self.assertFalse(self.svc._verify_password("nope", "salt", h))

    def test_upgrade_only_for_legacy(self):
        calls = []
        with patch.object(self.svc, "_db", side_effect=lambda: calls.append(1) or contextlib.nullcontext(MagicMock())):
            self.svc._maybe_upgrade_hash("u", "pw", self.svc._hash_password_bcrypt("pw"))
            self.assertEqual(calls, [])
            self.svc._maybe_upgrade_hash("u", "pw", "legacyhex")
            self.assertEqual(calls, [1])

    def test_mcp_token_cap(self):
        captured = {}

        class Cur:
            def execute(self, sql, params=None):
                captured["exp"] = params[-1]
        conn = MagicMock(); conn.cursor.return_value = Cur()
        with patch.object(self.svc, "_db", return_value=contextlib.nullcontext(conn)), \
             patch.object(self.svc, "require_auth", return_value="u1"):
            self.svc.create_mcp_token(self.svc.MCPTokenRequest(expires_days=3650), "Bearer x")
        delta = captured["exp"] - datetime.now(timezone.utc)
        self.assertLessEqual(delta, timedelta(days=settings.MCP_TOKEN_MAX_DAYS, seconds=5))


class FakeCursor:
    def __init__(self, results):
        self.results, self.sql, self.rowcount = list(results), [], 1

    def execute(self, sql, params=None):
        self.sql.append((sql, params))

    def fetchone(self):
        return self.results.pop(0) if self.results else None


def _db(cursor):
    conn = MagicMock(); conn.cursor.return_value = cursor
    return lambda: contextlib.nullcontext(conn)


def _form(**kw):
    return urlencode(kw)


class TestOAuth(unittest.TestCase):
    def setUp(self):
        self.cfg = _Settings(GPT_OAUTH_CLIENT_ID="cid", GPT_OAUTH_CLIENT_SECRET="csec",
                             GPT_OAUTH_REDIRECT_URIS="https://chatgpt.com/aip/g-1/oauth/callback",
                             GPT_REQUIRE_PKCE=True, **GPT_CFG)
        self.cfg.__enter__()
        self.t = tokens.request_ip.set("203.0.113.7")
        tokens._mem.clear()
        self.alert = patch.object(oauth, "security_alert").start()

    def tearDown(self):
        patch.stopall(); self.cfg.__exit__(); tokens.request_ip.reset(self.t)

    def test_redirect_uri_must_match_exactly(self):
        with self.assertRaises(HTTPException):
            oauth._validate_client("cid", "https://evil.example/cb")
        oauth._validate_client("cid", "https://chatgpt.com/aip/g-1/oauth/callback")

    def test_unknown_client_rejected(self):
        with self.assertRaises(HTTPException):
            oauth._validate_client("other", "https://chatgpt.com/aip/g-1/oauth/callback")

    def test_write_scope_cannot_be_requested(self):
        from services.auth.tokens import GPT_ALLOWED_SCOPES
        client = {"allowed": GPT_ALLOWED_SCOPES, "default": ["cases:read"], "kind": "gpt", "pkce": False}
        with self.assertRaises(HTTPException):
            oauth._parse_scopes(client, "cases:read cases:write")
        self.assertEqual(oauth._parse_scopes(client, "cases:read"), ["cases:read"])

    def test_authorize_requires_pkce(self):
        with self.assertRaises(HTTPException):
            oauth.authorize(response_type="code", client_id="cid",
                            redirect_uri="https://chatgpt.com/aip/g-1/oauth/callback",
                            scope="cases:read", state="s", code_challenge="", code_challenge_method="S256")

    def _request(self, body, headers=None):
        req = MagicMock()
        async def b(): return body.encode()
        req.body = b
        req.headers = headers or {}
        return req

    def _call_token(self, body):
        return asyncio.run(oauth.token(self._request(body)))

    def test_token_rejects_bad_client_secret(self):
        r = self._call_token(_form(grant_type="refresh_token", refresh_token="x",
                                   client_id="cid", client_secret="WRONG"))
        self.assertEqual(r.status_code, 401)

    def test_token_rejects_non_openai_ip(self):
        tokens.request_ip.set("198.51.100.9")
        r = self._call_token(_form(grant_type="refresh_token", refresh_token="x",
                                   client_id="cid", client_secret="csec"))
        self.assertEqual(r.status_code, 403)

    def test_code_exchange_pkce_ok_and_bad_verifier(self):
        verifier = "v" * 50
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        row = ("u1", "cid", "https://chatgpt.com/aip/g-1/oauth/callback", ["cases:read"], challenge,
               datetime.now(timezone.utc) + timedelta(seconds=30))
        user = ("u1", "a@b.c", "A", "user", True)
        redirect = "https://chatgpt.com/aip/g-1/oauth/callback"

        cur = FakeCursor([row, user])
        with patch.object(oauth, "get_db", _db(cur)):
            r = self._call_token(_form(grant_type="authorization_code", code="c", code_verifier=verifier,
                                       redirect_uri=redirect, client_id="cid", client_secret="csec"))
        self.assertEqual(r.status_code, 200)
        import json
        body = json.loads(r.body)
        self.assertEqual(body["expires_in"], 900)
        self.assertEqual(body["scope"], "cases:read")
        claims = tokens.authenticate  # decode to inspect
        from jose import jwt
        c = jwt.decode(body["access_token"], settings.JWT_SECRET, algorithms=["HS256"], audience=tokens.AUD_GPT)
        self.assertEqual(c["scopes"], ["cases:read"])
        self.assertIn("gid", c)

        cur = FakeCursor([row])
        with patch.object(oauth, "get_db", _db(cur)):
            r = self._call_token(_form(grant_type="authorization_code", code="c", code_verifier="wrong",
                                       redirect_uri=redirect, client_id="cid", client_secret="csec"))
        self.assertEqual(r.status_code, 400)

    def test_code_is_single_use(self):
        cur = FakeCursor([])  # DELETE ... RETURNING finds nothing the second time
        with patch.object(oauth, "get_db", _db(cur)):
            r = self._call_token(_form(grant_type="authorization_code", code="used", code_verifier="v",
                                       redirect_uri="https://chatgpt.com/aip/g-1/oauth/callback",
                                       client_id="cid", client_secret="csec"))
        self.assertEqual(r.status_code, 400)

    def _refresh_row(self, used_ago):
        now = datetime.now(timezone.utc)
        used = None if used_ago is None else now - timedelta(seconds=used_ago)
        return ("rt1", "fam1", used, now + timedelta(days=5), "u1", ["cases:read"], None, "cid")

    def test_refresh_rotation_ok(self):
        cur = FakeCursor([self._refresh_row(None), ("u1", "a@b.c", "A", "user", True)])
        with patch.object(oauth, "get_db", _db(cur)):
            r = self._call_token(_form(grant_type="refresh_token", refresh_token="r",
                                       client_id="cid", client_secret="csec"))
        self.assertEqual(r.status_code, 200)
        self.assertTrue(any("SET used_at" in s for s, _ in cur.sql))

    def test_refresh_reuse_revokes_grant_and_alerts(self):
        cur = FakeCursor([self._refresh_row(3600), ("u1", "a@b.c", "A", "user", True)])
        with patch.object(oauth, "get_db", _db(cur)):
            r = self._call_token(_form(grant_type="refresh_token", refresh_token="r",
                                       client_id="cid", client_secret="csec"))
        self.assertEqual(r.status_code, 400)
        self.assertTrue(any("revoked_at=now()" in s for s, _ in cur.sql))
        self.assertEqual(self.alert.call_args.args[0], "oauth_refresh_token_reuse")
        self.assertTrue(self.alert.call_args.kwargs.get("notify_user"))

    def test_refresh_reuse_within_grace_does_not_revoke(self):
        cur = FakeCursor([self._refresh_row(2)])
        with patch.object(oauth, "get_db", _db(cur)):
            r = self._call_token(_form(grant_type="refresh_token", refresh_token="r",
                                       client_id="cid", client_secret="csec"))
        self.assertEqual(r.status_code, 400)
        self.assertFalse(any("revoked_at=now()" in s for s, _ in cur.sql))

    def test_refresh_for_revoked_grant(self):
        row = list(self._refresh_row(None)); row[6] = datetime.now(timezone.utc)
        cur = FakeCursor([tuple(row)])
        with patch.object(oauth, "get_db", _db(cur)):
            r = self._call_token(_form(grant_type="refresh_token", refresh_token="r",
                                       client_id="cid", client_secret="csec"))
        self.assertEqual(r.status_code, 400)


class TestWebRefreshReuse(unittest.TestCase):
    def setUp(self):
        import importlib
        with patch("psycopg2.connect", side_effect=Exception("no db")):
            import services.auth.service as svc
            self.svc = importlib.reload(svc)
        self.alert = patch.object(self.svc, "security_alert").start()

    def tearDown(self):
        patch.stopall()

    def _call(self, used_ago):
        now = datetime.now(timezone.utc)
        used = None if used_ago is None else now - timedelta(seconds=used_ago)
        row = ("rt1", "fam1", used, now + timedelta(days=5), "u1", "a@b.c", "A", "user", True)
        cur = FakeCursor([row])
        req = MagicMock(); req.cookies = {"iai_refresh": "raw"}
        resp = MagicMock()
        with patch.object(self.svc, "_db", _db(cur)):
            try:
                out = self.svc.auth_refresh(req, resp)
                return out, None, cur
            except HTTPException as e:
                return None, e, cur

    def test_reuse_kills_family_and_alerts(self):
        out, err, cur = self._call(3600)
        self.assertEqual(err.status_code, 401)
        self.assertTrue(any("DELETE FROM refresh_tokens WHERE family_id" in s for s, _ in cur.sql))
        self.assertEqual(self.alert.call_args.args[0], "refresh_token_reuse")

    def test_grace_window_keeps_session(self):
        out, err, cur = self._call(2)
        self.assertEqual(err.status_code, 401)
        self.assertFalse(any("DELETE FROM refresh_tokens WHERE family_id" in s for s, _ in cur.sql))
        self.assertFalse(self.alert.called)

    def test_normal_rotation_marks_used(self):
        with patch.object(self.svc, "_issue", return_value={
                "access_token": "a", "refresh_token": "r2", "token_type": "bearer", "expires_in": 3600}) as issue:
            now = datetime.now(timezone.utc)
            row = ("rt1", "fam1", None, now + timedelta(days=5), "u1", "a@b.c", "A", "user", True)
            cur = FakeCursor([row])
            req = MagicMock(); req.cookies = {"iai_refresh": "raw"}
            with patch.object(self.svc, "_db", _db(cur)):
                out = self.svc.auth_refresh(req, MagicMock())
        self.assertEqual(out["access_token"], "a")
        self.assertEqual(issue.call_args.kwargs["family_id"], "fam1")
        self.assertTrue(any("SET used_at" in s for s, _ in cur.sql))


if __name__ == "__main__":
    unittest.main()
