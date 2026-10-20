"""
Smoke tests against the production API at https://api.probonoai.com.au
Run with: pytest tests/test_api_smoke.py -v -s
"""

import json
import warnings

import pytest
import requests

from conftest import BASE_URL, DEMO_PASSWORD

# Module-level token state shared across tests
access_token = None
refresh_token = None
# The refresh token lives in an httpOnly `iai_refresh` cookie (path=/auth), not
# in the JSON body; a Session carries it to /auth/refresh and /auth/logout.
session = requests.Session()

TIMEOUT = 30


def auth_headers():
    return {"Authorization": f"Bearer {access_token}"}


# ---------------------------------------------------------------------------
# Test 1 — Health check
# ---------------------------------------------------------------------------

def test_01_health():
    resp = requests.get(f"{BASE_URL}/health", timeout=TIMEOUT)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body.get("status") == "ok", f"Expected status='ok', got: {body}"


# ---------------------------------------------------------------------------
# Test 2 — Login as demo user
# ---------------------------------------------------------------------------

def test_02_login_demo():
    global access_token, refresh_token
    resp = session.post(
        f"{BASE_URL}/auth/login",
        json={"username": "demo", "password": DEMO_PASSWORD},
        timeout=TIMEOUT,
    )
    assert resp.status_code == 200, f"Login failed ({resp.status_code}): {resp.text}"
    body = resp.json()
    access_token = body.get("access_token")
    refresh_token = session.cookies.get("iai_refresh")
    assert access_token, f"No access_token in response: {body}"
    assert "refresh_token" not in body, "refresh token must not be exposed in the JSON body"
    assert refresh_token, "No iai_refresh cookie set by /auth/login"


# ---------------------------------------------------------------------------
# Test 3 — /auth/me
# ---------------------------------------------------------------------------

def test_03_me():
    if access_token is None:
        pytest.skip("Login failed — skipping test_03_me")
    resp = requests.get(f"{BASE_URL}/auth/me", headers=auth_headers(), timeout=TIMEOUT)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert "username" in body, f"Missing 'username' in: {body}"
    assert "name" in body, f"Missing 'name' in: {body}"
    assert "role" in body, f"Missing 'role' in: {body}"


# ---------------------------------------------------------------------------
# Test 4 — Token refresh
# ---------------------------------------------------------------------------

def test_04_refresh():
    global access_token
    if refresh_token is None:
        pytest.skip("Login failed — skipping test_04_refresh")
    resp = session.post(f"{BASE_URL}/auth/refresh", timeout=TIMEOUT)
    assert resp.status_code == 200, f"Refresh failed ({resp.status_code}): {resp.text}"
    body = resp.json()
    new_token = body.get("access_token")
    assert new_token, f"No access_token in refresh response: {body}"
    access_token = new_token


# ---------------------------------------------------------------------------
# Test 5 — Search legislation
# ---------------------------------------------------------------------------

def test_05_search_legislation():
    if access_token is None:
        pytest.skip("Login failed — skipping test_05_search_legislation")
    resp = requests.post(
        f"{BASE_URL}/search",
        json={"query": "bail conditions", "source": "legislation", "k": 3},
        headers=auth_headers(),
        timeout=TIMEOUT,
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()
    results = body.get("results", body) if isinstance(body, dict) else body
    assert isinstance(results, list), f"Expected results to be a list, got: {type(results)}"
    if len(results) == 0:
        warnings.warn("Search returned 0 results for 'bail conditions' / legislation", stacklevel=2)
    else:
        print(f"\n  [legislation] {len(results)} result(s) returned")


# ---------------------------------------------------------------------------
# Test 6 — Search caselaw
# ---------------------------------------------------------------------------

def test_06_search_caselaw():
    if access_token is None:
        pytest.skip("Login failed — skipping test_06_search_caselaw")
    resp = requests.post(
        f"{BASE_URL}/search",
        json={"query": "domestic violence", "source": "caselaw", "k": 3},
        headers=auth_headers(),
        timeout=TIMEOUT,
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()
    results = body.get("results", body) if isinstance(body, dict) else body
    assert isinstance(results, list), f"Expected results to be a list, got: {type(results)}"
    print(f"\n  [caselaw] {len(results)} result(s) returned")


# ---------------------------------------------------------------------------
# Test 7 — /ask SSE streaming
# ---------------------------------------------------------------------------

def test_07_ask_streams():
    if access_token is None:
        pytest.skip("Login failed — skipping test_07_ask_streams")
    resp = requests.post(
        f"{BASE_URL}/ask",
        json={"question": "What is bail?", "source": "legislation", "k": 3},
        headers=auth_headers(),
        stream=True,
        timeout=TIMEOUT,
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

    text_chunks = []
    for raw_line in resp.iter_lines():
        if not raw_line:
            continue
        line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line
        if not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if payload == "[DONE]":
            break
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            continue
        text = data.get("text", "")
        if text:
            text_chunks.append(text)

    assert len(text_chunks) > 0, "SSE stream returned no non-empty text chunks"
    print(f"\n  [ask] received {len(text_chunks)} text chunk(s); first: {text_chunks[0][:80]!r}")


# ---------------------------------------------------------------------------
# Test 8 — Logout
# ---------------------------------------------------------------------------

def test_08_logout():
    if refresh_token is None:
        pytest.skip("Login failed — skipping test_08_logout")
    resp = session.post(f"{BASE_URL}/auth/logout", headers=auth_headers(), timeout=TIMEOUT)
    assert resp.status_code == 200, f"Logout failed ({resp.status_code}): {resp.text}"
    body = resp.json()
    assert body.get("ok") is True, f"Expected ok=true in logout response: {body}"


# ---------------------------------------------------------------------------
# Test 9 — /auth/me after logout (JWT is stateless — accept 200 or 401)
# ---------------------------------------------------------------------------

def test_09_me_after_logout():
    if access_token is None:
        pytest.skip("Login failed — skipping test_09_me_after_logout")
    resp = requests.get(f"{BASE_URL}/auth/me", headers=auth_headers(), timeout=TIMEOUT)
    assert resp.status_code in (200, 401), (
        f"Expected 200 or 401 after logout, got {resp.status_code}: {resp.text}"
    )
    print(f"\n  [me-after-logout] status={resp.status_code} (200 or 401 both acceptable)")
