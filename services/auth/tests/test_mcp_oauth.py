"""Tests for the ChatGPT MCP-connector OAuth path (no DB / network)."""
import asyncio
import json
import unittest
from datetime import timedelta
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from services.auth import oauth, tokens, wellknown
from services.core.settings import settings
from services.auth.tests.test_gpt_hardening import GPT_CFG, _Settings

MCP_URL = "https://api.probonoai.com.au/mcp"
REDIRECT = "https://chatgpt.com/connector_platform_oauth_redirect"
CFG = dict(GPT_CFG, MCP_RESOURCE_URL=MCP_URL, OAUTH_ISSUER="https://api.probonoai.com.au",
           MCP_OAUTH_CLIENT_ID="mcpcid", MCP_OAUTH_CLIENT_SECRET="mcpsec",
           MCP_OAUTH_REDIRECT_URIS=REDIRECT, GPT_OAUTH_CLIENT_ID="")


def _mcp_token(scopes=("search",), aud=MCP_URL, iss="https://api.probonoai.com.au", gid="g1"):
    return tokens.make_access_token(sub="u1", email="a@b.c", name="A", role="user", scopes=list(scopes),
                                    aud=aud, ttl=timedelta(minutes=15), grant_id=gid, iss=iss)


class TestMcpAuth(unittest.TestCase):
    def setUp(self):
        tokens._mem.clear()
        self.cfg = _Settings(**CFG); self.cfg.__enter__()
        self.t = tokens.request_ip.set("203.0.113.7")
        patch.object(tokens, "_grant_active", return_value=True).start()
        patch.object(tokens, "security_alert").start()

    def tearDown(self):
        patch.stopall(); self.cfg.__exit__(); tokens.request_ip.reset(self.t)

    def test_happy_path(self):
        self.assertEqual(tokens.authenticate_mcp(_mcp_token(), "search")["sub"], "u1")

    def test_wrong_audience_rejected(self):
        with self.assertRaises(HTTPException) as e:
            tokens.authenticate_mcp(_mcp_token(aud=tokens.AUD_WEB))
        self.assertEqual(e.exception.status_code, 401)

    def test_wrong_issuer_rejected(self):
        with self.assertRaises(HTTPException):
            tokens.authenticate_mcp(_mcp_token(iss="evil"))

    def test_scope_not_granted(self):
        with self.assertRaises(HTTPException) as e:
            tokens.authenticate_mcp(_mcp_token(scopes=("search",)), "ask")
        self.assertEqual(e.exception.status_code, 403)

    def test_write_scope_never_allowed(self):
        with self.assertRaises(HTTPException):
            tokens.authenticate_mcp(_mcp_token(scopes=("cases:write",)), "cases:write")

    def test_wrong_ip(self):
        tokens.request_ip.set("198.51.100.9")
        with self.assertRaises(HTTPException) as e:
            tokens.authenticate_mcp(_mcp_token(), "search")
        self.assertEqual(e.exception.status_code, 403)

    def test_revoked_grant(self):
        with patch.object(tokens, "_grant_active", return_value=False):
            with self.assertRaises(HTTPException) as e:
                tokens.authenticate_mcp(_mcp_token(), "search")
        self.assertEqual(e.exception.status_code, 401)

    def test_mcp_token_not_valid_for_api(self):
        with self.assertRaises(HTTPException):
            tokens.authenticate("Bearer " + _mcp_token())


class TestMcpOAuthEndpoints(unittest.TestCase):
    def setUp(self):
        self.cfg = _Settings(**CFG); self.cfg.__enter__()

    def tearDown(self):
        self.cfg.__exit__()

    def test_redirect_exact(self):
        oauth._validate_client("mcpcid", REDIRECT)
        with self.assertRaises(HTTPException):
            oauth._validate_client("mcpcid", "https://evil.example/cb")

    def test_resource_must_be_mcp_url(self):
        c = oauth._find_client("mcpcid")
        oauth._check_resource(c, MCP_URL)
        oauth._check_resource(c, "")
        with self.assertRaises(HTTPException):
            oauth._check_resource(c, "https://evil.example/mcp")

    def test_scopes_limited(self):
        c = oauth._find_client("mcpcid")
        with self.assertRaises(HTTPException):
            oauth._parse_scopes(c, "search conversations:read")
        self.assertEqual(oauth._parse_scopes(c, "search ask"), ["ask", "search"])

    def test_discovery_documents(self):
        pr = json.loads(wellknown.protected_resource().body)
        self.assertEqual(pr["resource"], MCP_URL)
        self.assertEqual(pr["authorization_servers"], ["https://api.probonoai.com.au"])
        asm = json.loads(wellknown.authorization_server().body)
        self.assertEqual(asm["code_challenge_methods_supported"], ["S256"])
        self.assertTrue(asm["authorization_response_iss_parameter_supported"])
        self.assertEqual(asm["token_endpoint"], "https://api.probonoai.com.au/oauth/token")


if __name__ == "__main__":
    unittest.main()
