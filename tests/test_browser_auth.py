from __future__ import annotations

import json

import pytest
import requests

from snow_api_browser.browser_auth import BrowserSessionClient
from snow_api_browser.config import BrowserAuthConfig


class FakePage:
    def __init__(self, url, session):
        self.url = url
        self._session = session
        self.goto_calls = []

    def goto(self, url, wait_until, timeout):
        self.goto_calls.append((url, wait_until, timeout))

    def evaluate(self, expression):
        if expression == "() => navigator.userAgent":
            return "TestAgent/1.0"
        return self._session


class FakeContext:
    def __init__(self, cookies=None):
        self._cookies = cookies or []
        self.added_cookies = None

    def add_cookies(self, cookies):
        self.added_cookies = cookies

    def cookies(self):
        return self._cookies


def test_ensure_authenticated_accepts_servicenow_page_with_user_token():
    client = BrowserSessionClient(
        "https://example.service-now.com",
        BrowserAuthConfig(profile_path="profile", headless=True),
    )
    page = FakePage(
        "https://example.service-now.com/now/nav/ui/classic/params/target/home.do",
        {
            "href": "https://example.service-now.com/now/nav/ui/classic/params/target/home.do",
            "userToken": "token-123",
            "transactionSource": "test-user",
        },
    )

    session = client._ensure_authenticated(page)

    assert session["userToken"] == "token-123"
    assert page.goto_calls == []


def test_ensure_authenticated_rejects_okta_saml_page(monkeypatch):
    monkeypatch.setattr("snow_api_browser.browser_auth.time.sleep", lambda *_: None)

    client = BrowserSessionClient(
        "https://example.service-now.com",
        BrowserAuthConfig(profile_path="profile", headless=True),
    )
    client.timeout_ms = 1
    page = FakePage(
        "https://example.okta.com/app/test/sso/saml",
        {
            "href": "https://example.okta.com/app/test/sso/saml",
            "userToken": None,
            "transactionSource": "/app/test/sso/saml",
        },
    )

    with pytest.raises(requests.exceptions.RequestException, match="not authenticated"):
        client._ensure_authenticated(page)

    assert page.goto_calls == []


def test_save_and_restore_cookies(tmp_path):
    client = BrowserSessionClient(
        "https://example.service-now.com",
        BrowserAuthConfig(profile_path=str(tmp_path), headless=True),
    )
    client._context = FakeContext(
        cookies=[{"name": "sid", "value": "abc", "domain": "example.com", "path": "/"}]
    )

    client._save_cookies()

    payload = json.loads((tmp_path / "servicenow-browser-cookies.json").read_text(encoding="utf-8"))
    assert payload["cookies"][0]["name"] == "sid"

    restored = BrowserSessionClient(
        "https://example.service-now.com",
        BrowserAuthConfig(profile_path=str(tmp_path), headless=True),
    )
    restored._context = FakeContext()
    restored._restore_cookies()

    assert restored._context.added_cookies == [
        {"name": "sid", "value": "abc", "domain": "example.com", "path": "/"}
    ]


def test_send_request_with_session_replays_browser_cookies(monkeypatch, tmp_path):
    client = BrowserSessionClient(
        "https://example.service-now.com",
        BrowserAuthConfig(profile_path=str(tmp_path), headless=True),
    )
    client._context = FakeContext(
        cookies=[{"name": "sid", "value": "abc", "domain": "example.service-now.com", "path": "/"}]
    )

    captured = {}

    class FakeSession:
        def request(self, method, url, **kwargs):
            captured["method"] = method
            captured["url"] = url
            captured["headers"] = kwargs["headers"]
            captured["cookies"] = kwargs["cookies"]
            response = requests.Response()
            response.status_code = 200
            response._content = b"{}"
            response.url = url
            response.request = requests.Request(method=method, url=url).prepare()
            return response

        def close(self):
            return None

    monkeypatch.setattr("snow_api_browser.browser_auth.requests.Session", FakeSession)

    response = client._send_request_with_session(
        method="GET",
        url="https://example.service-now.com/api/now/table/incident",
        headers={"Accept": "application/json"},
        payload=None,
        timeout=30,
        allow_redirects=True,
    )

    assert response.status_code == 200
    assert captured["method"] == "GET"
    assert captured["url"] == "https://example.service-now.com/api/now/table/incident"
    assert captured["headers"]["Accept"] == "application/json"
    assert captured["cookies"].get("sid", domain="example.service-now.com", path="/") == "abc"


def test_ensure_authenticated_or_login_falls_back_to_visible_login(monkeypatch):
    client = BrowserSessionClient(
        "https://example.service-now.com",
        BrowserAuthConfig(profile_path="profile", headless=True),
    )

    calls = []

    def fake_start():
        calls.append("start")

    def fake_require_page():
        return object()

    def fake_ensure_authenticated(page):
        del page
        calls.append("ensure")
        raise requests.exceptions.RequestException("not authenticated")

    def fake_launch_interactive_login():
        calls.append("interactive")
        return {"userToken": "token-123", "transactionSource": "test-user"}

    monkeypatch.setattr(client, "start", fake_start)
    monkeypatch.setattr(client, "_require_page", fake_require_page)
    monkeypatch.setattr(client, "_ensure_authenticated", fake_ensure_authenticated)
    monkeypatch.setattr(client, "_launch_interactive_login", fake_launch_interactive_login)

    session = client.ensure_authenticated_or_login()

    assert session["userToken"] == "token-123"
    assert calls == ["start", "ensure", "interactive"]


def test_request_uses_auth_guard(monkeypatch):
    client = BrowserSessionClient(
        "https://example.service-now.com",
        BrowserAuthConfig(profile_path="profile", headless=True),
    )
    page = FakePage(
        "https://example.service-now.com/home.do",
        {"href": "https://example.service-now.com/home.do"},
    )

    monkeypatch.setattr(client, "_load_saved_session", lambda: None)
    monkeypatch.setattr(
        client,
        "_refresh_session",
        lambda **kwargs: {"userToken": "token-123", "transactionSource": "test-user"},
    )
    monkeypatch.setattr(
        client,
        "_launch_interactive_login",
        lambda: (_ for _ in ()).throw(AssertionError("should not launch interactive login")),
    )
    monkeypatch.setattr(client, "_require_page", lambda: page)
    monkeypatch.setattr(client, "_build_payload", lambda data, json_body: None)
    monkeypatch.setattr(client, "_append_params", lambda url, params: url)
    monkeypatch.setattr(client, "_response_requires_login", lambda response: False)

    captured = {}

    def fake_send_request_with_session(**kwargs):
        captured.update(kwargs)
        response = requests.Response()
        response.status_code = 200
        response._content = b"{}"
        response.url = kwargs["url"]
        response.request = requests.Request(method=kwargs["method"], url=kwargs["url"]).prepare()
        return response

    monkeypatch.setattr(client, "_send_request_with_session", fake_send_request_with_session)

    response = client.request("GET", "https://example.service-now.com/api/now/table/sys_user")

    assert response.status_code == 200
    assert captured["headers"]["X-UserToken"] == "token-123"
    assert captured["headers"]["Referer"] == "https://example.service-now.com/home.do"


def test_request_uses_saved_state_before_browser(monkeypatch, tmp_path):
    client = BrowserSessionClient(
        "https://example.service-now.com",
        BrowserAuthConfig(profile_path=str(tmp_path), headless=True),
    )

    monkeypatch.setattr(
        client,
        "_load_saved_session",
        lambda: {
            "userToken": "token-123",
            "transactionSource": "test-user",
            "userAgent": "SavedAgent/1.0",
            "referer": "https://example.service-now.com/home.do",
        },
    )
    monkeypatch.setattr(
        client,
        "_load_saved_cookies",
        lambda: [{"name": "sid", "value": "abc", "domain": "example.service-now.com", "path": "/"}],
    )

    captured = {}

    def fake_send_request_with_cookies(**kwargs):
        captured["headers"] = kwargs["headers"]
        response = requests.Response()
        response.status_code = 200
        response._content = b"{}"
        response.url = kwargs["url"]
        response.request = requests.Request(method=kwargs["method"], url=kwargs["url"]).prepare()
        response.headers["Content-Type"] = "application/json"
        return response

    monkeypatch.setattr(client, "_send_request_with_cookies", fake_send_request_with_cookies)
    monkeypatch.setattr(
        client,
        "_refresh_session",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("should not refresh browser session")
        ),
    )
    monkeypatch.setattr(
        client,
        "_launch_interactive_login",
        lambda: (_ for _ in ()).throw(AssertionError("should not launch interactive login")),
    )

    response = client.request("GET", "https://example.service-now.com/api/now/table/sys_user")

    assert response.status_code == 200
    assert captured["headers"]["X-UserToken"] == "token-123"
    assert captured["headers"]["User-Agent"] == "SavedAgent/1.0"
