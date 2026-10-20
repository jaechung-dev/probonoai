"""Auth rate limits are enforced without Redis (production has none).

Before this, _rate_limit returned early when Redis was unavailable, so login
and OTP brute-force limits were no-ops in production, and the email-sending
endpoints had no limit at all.
"""
import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from services.auth.tests.test_auth import _import_service


class TestRateLimitHelpers(unittest.TestCase):
    def setUp(self):
        self.svc = _import_service()

    def test_async_limit_uses_shared_counter_and_raises_429(self):
        with patch.object(self.svc.ratelimit, "hit", AsyncMock(return_value=False)) as hit:
            with self.assertRaises(HTTPException) as e:
                asyncio.run(self.svc._rate_limit("rate:login:a@b.c"))
        self.assertEqual(e.exception.status_code, 429)
        hit.assert_awaited_once_with("rate:login:a@b.c", 5, 900)

    def test_async_limit_allows_within_limit(self):
        with patch.object(self.svc.ratelimit, "hit", AsyncMock(return_value=True)):
            asyncio.run(self.svc._rate_limit("rate:login:a@b.c"))  # no raise

    def test_no_redis_no_longer_means_no_limit(self):
        # The old code returned early here. Now it falls through to Postgres.
        with patch.object(self.svc.ratelimit, "get_redis", AsyncMock(return_value=None)), \
             patch.object(self.svc.ratelimit, "_hit_postgres", return_value=6) as pg:
            with self.assertRaises(HTTPException):
                asyncio.run(self.svc._rate_limit("rate:login:a@b.c"))
        pg.assert_called_once()


class TestEmailSendLimits(unittest.TestCase):
    """register / resend-verification / forgot-password are capped per address and per IP."""

    def setUp(self):
        self.svc = _import_service()
        app = FastAPI()
        app.include_router(self.svc.router)
        self.client = TestClient(app, raise_server_exceptions=False)

    def _post(self, path, body, allowed):
        calls = []

        def fake_hit(key, limit, window):
            calls.append((key, limit, window))
            return allowed

        with patch.object(self.svc.ratelimit, "hit_sync", side_effect=fake_hit), \
             patch.object(self.svc, "_db_user", return_value=None), \
             patch.object(self.svc, "_send_email") as send:
            resp = self.client.post(path, json=body)
        return resp, calls, send

    def test_each_email_endpoint_returns_429_over_limit_without_sending(self):
        for path, body in (
            ("/auth/forgot-password", {"email": "victim@example.com"}),
            ("/auth/resend-verification", {"email": "victim@example.com"}),
            ("/auth/register", {"email": "victim@example.com", "name": "X", "password": "longenough1"}),
        ):
            resp, calls, send = self._post(path, body, allowed=False)
            self.assertEqual(resp.status_code, 429, path)
            send.assert_not_called()
            self.assertEqual(calls[0][0], "rate:email-send:victim@example.com", path)

    def test_limits_are_per_address_and_per_ip(self):
        resp, calls, _ = self._post("/auth/forgot-password", {"email": "Victim@Example.com"}, allowed=True)
        self.assertEqual(resp.status_code, 200)
        keys = [c[0] for c in calls]
        self.assertEqual(keys[0], "rate:email-send:victim@example.com")  # normalised
        self.assertTrue(keys[1].startswith("rate:email-send-ip:"))
        self.assertEqual([c[1:] for c in calls], [(3, 3600), (10, 3600)])

    def test_unknown_email_still_counted_so_429_does_not_reveal_accounts(self):
        # _db_user returns None (no such account) but the limit still applies.
        resp, calls, _ = self._post("/auth/forgot-password", {"email": "nobody@example.com"}, allowed=False)
        self.assertEqual(resp.status_code, 429)


class TestLoginLimits(unittest.TestCase):
    """Only failures count per account; checked before the password is verified."""

    def setUp(self):
        self.svc = _import_service()
        app = FastAPI()
        app.include_router(self.svc.router)
        self.client = TestClient(app, raise_server_exceptions=False)
        self.user = {"id": "u1", "provider": "password", "salt": "", "password_hash": "h",
                     "name": "U", "role": "user", "email_verified": True}

    def _login(self, username, password, *, counts=None, verify=True, user=True):
        counts = counts or {}
        hits = []

        async def fake_count(key):
            return counts.get(key.split(":")[1], 0)  # "login-fail"

        async def fake_hit(key, limit, window):
            hits.append(key)
            return True

        with patch.object(self.svc.ratelimit, "count", side_effect=fake_count), \
             patch.object(self.svc.ratelimit, "hit", side_effect=fake_hit), \
             patch.object(self.svc, "_db_user", return_value=self.user if user else None), \
             patch.object(self.svc, "_verify_password", return_value=verify), \
             patch.object(self.svc, "_maybe_upgrade_hash"), \
             patch.object(self.svc, "_issue", return_value={"access_token": "a", "refresh_token": "r", "token_type": "bearer", "expires_in": 1}), \
             patch.object(self.svc, "_log_access"):
            resp = self.client.post("/auth/login", json={"username": username, "password": password})
        return resp, hits

    def test_successful_login_does_not_count_against_the_account(self):
        resp, hits = self._login("a@b.c", "right")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse([h for h in hits if "login-fail" in h])

    def test_failed_login_counts_per_ip_and_per_account(self):
        resp, hits = self._login("a@b.c", "wrong", verify=False)
        self.assertEqual(resp.status_code, 401)
        self.assertTrue(any(h.startswith("rate:login-fail:a@b.c:") for h in hits))
        self.assertIn("rate:login-fail:a@b.c", hits)

    def test_over_limit_blocks_even_a_correct_password(self):
        # Otherwise 200-vs-429 would reveal correct guesses.
        resp, _ = self._login("a@b.c", "right", counts={"login-fail": 99})
        self.assertEqual(resp.status_code, 429)

    def test_guest_demo_account_is_never_locked_account_wide(self):
        demo = next(iter(self.svc.SEED_USERS))
        resp, hits = self._login(demo, "wrong", verify=False, user=False)
        self.assertEqual(resp.status_code, 401)
        self.assertNotIn(f"rate:login-fail:{demo.lower()}", hits)  # account-wide key skipped


if __name__ == "__main__":
    unittest.main()
