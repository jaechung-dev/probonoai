"""Anonymous quotas on the AI Lambda (/chat, /ask, /search)."""
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient
from jose import jwt

with patch("langchain_openai.OpenAIEmbeddings",
           side_effect=lambda *a, **kw: MagicMock(embed_query=lambda q: [0.0] * 1536)), \
     patch("langchain_openai.ChatOpenAI", side_effect=lambda *a, **kw: MagicMock()):
    import services.ai.main as ai


class _StopAfterQuota(Exception):
    """Raised by a patched retriever: the request got past the quota check."""


def _token(sub="user-1"):
    return jwt.encode({"sub": sub, "aud": "probonoai-api"}, ai.JWT_SECRET, algorithm=ai.JWT_ALG)


CHAT = ("/chat", {"question": "What is a bond?", "messages": []})
ASK = ("/ask", {"question": "What is a bond?", "source": "legislation"})
SEARCH = ("/search", {"query": "bond", "source": "legislation"})


class TestAnonQuota(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(ai.app, raise_server_exceptions=False)
        # Any retrieval means the quota let the request through; stop there.
        for name in ("LegislationRetriever", "CaselawRetriever"):
            p = patch.object(ai, name, side_effect=_StopAfterQuota)
            p.start()
            self.addCleanup(p.stop)

    def _post(self, route, token=None, allowed=True):
        path, body = route
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        hit = AsyncMock(return_value=allowed)
        with patch.object(ai.ratelimit, "hit", hit):
            resp = self.client.post(path, json=body, headers=headers)
        return resp, hit

    def test_anon_llm_endpoints_get_401_over_quota(self):
        for route in (CHAT, ASK):
            resp, hit = self._post(route, allowed=False)
            self.assertEqual(resp.status_code, 401, route[0])
            self.assertIn("register or log in", resp.json()["detail"])
            self.assertTrue(hit.call_args.args[0].startswith("anon-llm:"))

    def test_chat_and_ask_share_one_counter(self):
        _, chat_hit = self._post(CHAT, allowed=False)
        _, ask_hit = self._post(ASK, allowed=False)
        self.assertEqual(chat_hit.call_args.args[0], ask_hit.call_args.args[0])

    def test_anon_search_gets_429_over_quota(self):
        resp, hit = self._post(SEARCH, allowed=False)
        self.assertEqual(resp.status_code, 429)
        self.assertTrue(hit.call_args.args[0].startswith("anon-search:"))

    def test_anon_within_quota_proceeds(self):
        resp, _ = self._post(CHAT, allowed=True)
        self.assertEqual(resp.status_code, 500)  # reached the (patched) retriever

    def test_logged_in_users_are_not_counted(self):
        for route in (CHAT, ASK, SEARCH):
            resp, hit = self._post(route, token=_token(), allowed=False)
            hit.assert_not_called()
            self.assertNotIn(resp.status_code, (401, 429), route[0])

    def test_invalid_token_is_treated_as_anonymous(self):
        resp, hit = self._post(CHAT, token="not-a-jwt", allowed=False)
        self.assertEqual(resp.status_code, 401)
        hit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
