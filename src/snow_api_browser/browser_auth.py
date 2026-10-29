from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests
from playwright.sync_api import sync_playwright
from requests import Response
from requests.exceptions import RequestException

from .config import BrowserAuthConfig


logger = logging.getLogger(__name__)
LOGIN_MARKERS = ("login.do", "signin", "oauth_redirect", "glide_sso_id", "/sso/saml")


class BrowserSessionClient:
    """Executes ServiceNow API requests using an authenticated browser session."""

    def __init__(
        self,
        instance_url: str,
        config: BrowserAuthConfig,
        timeout_seconds: int = 30,
    ) -> None:
        self.instance_url = instance_url.rstrip("/")
        self.config = config
        self.timeout_ms = max(timeout_seconds, 1) * 1000
        self._lock = threading.RLock()
        self._owner_thread_id: int | None = None
        self._playwright = None
        self._context = None
        self._page = None
        self._profile_dir = Path(config.profile_path).resolve()

    def start(self) -> None:
        with self._lock:
            if self._page and not self._page.is_closed():
                return

            self._owner_thread_id = threading.get_ident()
            self._profile_dir.mkdir(parents=True, exist_ok=True)

            args = [
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-blink-features=AutomationControlled",
                "--disable-features=Translate",
                "--disable-background-networking",
                "--disable-sync",
                "--disable-extensions",
                "--password-store=basic",
                "--use-mock-keychain",
            ]

            self._playwright = sync_playwright().start()
            self._context = self._playwright.chromium.launch_persistent_context(
                str(self._profile_dir),
                headless=self.config.headless,
                args=args,
                ignore_default_args=["--enable-automation"],
                viewport={"width": 1440, "height": 900},
            )
            self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
            self._restore_cookies()
            self._page.goto(self.login_url, wait_until="domcontentloaded", timeout=self.timeout_ms)
            try:
                self._page.wait_for_load_state("networkidle", timeout=min(self.timeout_ms, 15000))
            except Exception:
                logger.debug("Browser page did not reach networkidle before timeout")

    def close(self) -> None:
        with self._lock:
            if self._owner_thread_id is not None and threading.get_ident() != self._owner_thread_id:
                self._page = None
                self._context = None
                self._playwright = None
                self._owner_thread_id = None
                return

            self._save_cookies()

            if self._page is not None:
                try:
                    self._page.close()
                except Exception:
                    pass
                self._page = None

            if self._context is not None:
                try:
                    self._context.close()
                except Exception:
                    pass
                self._context = None

            if self._playwright is not None:
                try:
                    self._playwright.stop()
                except Exception:
                    pass
                self._playwright = None

            self._owner_thread_id = None

    @property
    def login_url(self) -> str:
        if self.config.login_path.startswith(("http://", "https://")):
            return self.config.login_path
        return f"{self.instance_url}{self.config.login_path}"

    def _session_path(self) -> Path:
        return self._profile_dir / "servicenow-browser-session.json"

    def ensure_authenticated(self) -> dict[str, str | None]:
        with self._lock:
            self.start()
            return self._ensure_authenticated(self._require_page())

    def ensure_authenticated_or_login(self) -> dict[str, str | None]:
        with self._lock:
            try:
                self.start()
                return self._ensure_authenticated(self._require_page())
            except RequestException:
                return self._launch_interactive_login()

    def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        data: Any = None,
        json_body: Any = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
        allow_redirects: bool = True,
    ) -> Response:
        with self._lock:
            payload = self._build_payload(data, json_body)
            request_url = self._append_params(url, params)

            saved_session = self._load_saved_session()
            if saved_session is not None:
                response = self._send_request_from_saved_state(
                    method=method,
                    url=request_url,
                    headers=headers or {},
                    json_body=json_body,
                    data=data,
                    payload=payload,
                    timeout=timeout,
                    allow_redirects=allow_redirects,
                    saved_session=saved_session,
                )
                if response is not None:
                    return response

            try:
                session = self._refresh_session(headless=True)
            except RequestException:
                session = self._launch_interactive_login()

            page = self._require_page()
            merged_headers = self._build_headers(session, headers or {}, json_body, data)
            merged_headers.setdefault("User-Agent", page.evaluate("() => navigator.userAgent"))
            merged_headers.setdefault("Referer", page.url or self.login_url)
            self._save_session(
                {
                    **session,
                    "userAgent": merged_headers["User-Agent"],
                    "referer": merged_headers["Referer"],
                }
            )

            response = self._send_request_with_session(
                method=method,
                url=request_url,
                headers=merged_headers,
                payload=payload,
                timeout=timeout,
                allow_redirects=allow_redirects,
            )
            if self._response_requires_login(response):
                session = self._launch_interactive_login()
                page = self._require_page()
                merged_headers = self._build_headers(session, headers or {}, json_body, data)
                merged_headers.setdefault("User-Agent", page.evaluate("() => navigator.userAgent"))
                merged_headers.setdefault("Referer", page.url or self.login_url)
                self._save_session(
                    {
                        **session,
                        "userAgent": merged_headers["User-Agent"],
                        "referer": merged_headers["Referer"],
                    }
                )
                response = self._send_request_with_session(
                    method=method,
                    url=request_url,
                    headers=merged_headers,
                    payload=payload,
                    timeout=timeout,
                    allow_redirects=allow_redirects,
                )

            response.raise_for_status()
            return response

    def _require_page(self):
        if not self._page or self._page.is_closed():
            raise RequestException("Browser session is not available")
        return self._page

    def _cookies_path(self) -> Path:
        return self._profile_dir / "servicenow-browser-cookies.json"

    def _restore_cookies(self) -> None:
        if self._context is None:
            return

        cookies_path = self._cookies_path()
        if not cookies_path.exists():
            return

        try:
            payload = json.loads(cookies_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Failed to read saved browser cookies from %s: %s", cookies_path, exc)
            return

        cookies = payload.get("cookies") if isinstance(payload, dict) else None
        if not cookies:
            return

        try:
            self._context.add_cookies(cookies)
        except Exception as exc:
            logger.warning("Failed to restore saved browser cookies from %s: %s", cookies_path, exc)

    def _save_cookies(self) -> None:
        if self._context is None:
            return

        cookies_path = self._cookies_path()
        try:
            cookies = self._context.cookies()
            cookies_path.write_text(json.dumps({"cookies": cookies}, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.warning("Failed to save browser cookies to %s: %s", cookies_path, exc)

    def _load_saved_cookies(self) -> list[dict[str, Any]]:
        cookies_path = self._cookies_path()
        if not cookies_path.exists():
            return []

        try:
            payload = json.loads(cookies_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Failed to read saved browser cookies from %s: %s", cookies_path, exc)
            return []

        cookies = payload.get("cookies") if isinstance(payload, dict) else None
        return cookies if isinstance(cookies, list) else []

    def _save_session(self, session: dict[str, str | None]) -> None:
        session_path = self._session_path()
        try:
            session_path.write_text(json.dumps(session, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.warning("Failed to save browser session to %s: %s", session_path, exc)

    def _load_saved_session(self) -> dict[str, str | None] | None:
        session_path = self._session_path()
        if not session_path.exists():
            return None

        try:
            payload = json.loads(session_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Failed to read saved browser session from %s: %s", session_path, exc)
            return None

        return payload if isinstance(payload, dict) else None

    def _ensure_authenticated(self, page) -> dict[str, str | None]:
        current_url = page.url or ""
        if not current_url or current_url == "about:blank":
            page.goto(self.login_url, wait_until="domcontentloaded", timeout=self.timeout_ms)

        deadline = time.monotonic() + min(self.timeout_ms, 15000) / 1000
        last_href = page.url or current_url
        last_error: Exception | None = None

        while time.monotonic() < deadline:
            try:
                session = page.evaluate(
                    f"""() => ({{
                        href: window.location.href,
                        userToken:
                            window.g_ck ||
                            window.NOW?.user_token ||
                            window.NOW?.g_ck ||
                            document.querySelector('meta[name="sysparm_ck"]')?.content ||
                            null,
                        transactionSource:
                            window.NOW?.transaction_source ||
                            window.NOW?.user?.userName ||
                            window.location.pathname ||
                            '{self.config.transaction_source}',
                    }})"""
                )
            except Exception as exc:
                last_error = exc
                time.sleep(0.5)
                continue

            href = str(session.get("href") or page.url or "")
            last_href = href
            normalized_href = href.lower()
            on_instance = href.startswith(self.instance_url)
            on_login_page = any(marker in normalized_href for marker in LOGIN_MARKERS)

            if on_instance and not on_login_page and session.get("userToken"):
                self._save_cookies()
                try:
                    session["userAgent"] = page.evaluate("() => navigator.userAgent")
                except Exception:
                    session.setdefault("userAgent", None)
                session["referer"] = href or self.login_url
                self._save_session(session)
                return session

            time.sleep(0.5)

        mode = (
            "Run the login CLI first or use a visible browser session."
            if self.config.headless
            else "Complete the visible browser login, then retry."
        )
        details = f"Current page: {last_href or 'unknown'}"
        if last_error is not None:
            details = f"{details}. Last browser error: {last_error}"
        raise RequestException(
            f"ServiceNow browser session is not authenticated. {details}. {mode}"
        )

    def _launch_interactive_login(self) -> dict[str, str | None]:
        logger.info("Browser session is not authenticated; launching visible sign-in flow")
        self.close()

        previous_headless = self.config.headless
        try:
            self.config.headless = False
            self.start()
            print("ServiceNow authentication is required. Complete sign-in in the browser window.")
            print(f"Profile path: {os.path.abspath(str(self._profile_dir))}")
            deadline = time.monotonic() + max(self.config.interactive_login_timeout_seconds, 1)
            last_error: RequestException | None = None

            while time.monotonic() < deadline:
                try:
                    session = self._ensure_authenticated(self._require_page())
                    self._save_cookies()
                    return session
                except RequestException as exc:
                    last_error = exc
                    time.sleep(2)

            if last_error is not None:
                raise RequestException(str(last_error)) from last_error
            raise RequestException("Timed out waiting for interactive ServiceNow authentication")
        finally:
            self.config.headless = previous_headless

    def _refresh_session(self, *, headless: bool) -> dict[str, str | None]:
        previous_headless = self.config.headless
        try:
            self.close()
            self.config.headless = headless
            self.start()
            return self._ensure_authenticated(self._require_page())
        finally:
            self.config.headless = previous_headless

    def _build_headers(
        self,
        session: dict[str, str | None],
        headers: dict[str, str],
        json_body: Any,
        data: Any,
    ) -> dict[str, str]:
        merged = {str(key): str(value) for key, value in headers.items() if value is not None}
        merged.setdefault("Accept", "application/json")

        user_token = session.get("userToken")
        if user_token:
            merged.setdefault("X-UserToken", user_token)

        transaction_source = session.get("transactionSource") or self.config.transaction_source
        merged.setdefault("X-Transaction-Source", transaction_source)

        if json_body is not None:
            merged.setdefault("Content-Type", "application/json")
        elif isinstance(data, dict):
            merged.setdefault("Content-Type", "application/x-www-form-urlencoded; charset=UTF-8")

        return merged

    def _build_payload(self, data: Any, json_body: Any) -> str | None:
        if json_body is not None:
            return json.dumps(json_body)
        if data is None:
            return None
        if isinstance(data, (bytes, bytearray)):
            return data.decode("utf-8")
        if isinstance(data, dict):
            return urlencode(data)
        return str(data)

    def _build_cookie_jar(
        self, cookies: list[dict[str, Any]]
    ) -> requests.cookies.RequestsCookieJar:
        jar = requests.cookies.RequestsCookieJar()
        for cookie in cookies:
            jar.set(
                cookie["name"],
                cookie["value"],
                domain=cookie.get("domain"),
                path=cookie.get("path", "/"),
            )
        return jar

    def _send_request_from_saved_state(
        self,
        *,
        method: str,
        url: str,
        headers: dict[str, str],
        json_body: Any,
        data: Any,
        payload: str | None,
        timeout: float | None,
        allow_redirects: bool,
        saved_session: dict[str, str | None],
    ) -> Response | None:
        cookies = self._load_saved_cookies()
        if not cookies:
            return None

        merged_headers = self._build_headers(saved_session, headers, json_body, data)
        merged_headers.setdefault("User-Agent", str(saved_session.get("userAgent") or ""))
        if not merged_headers["User-Agent"]:
            merged_headers["User-Agent"] = (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
            )
        merged_headers.setdefault(
            "Referer",
            str(saved_session.get("referer") or saved_session.get("href") or self.login_url),
        )

        response = self._send_request_with_cookies(
            method=method,
            url=url,
            headers=merged_headers,
            payload=payload,
            timeout=timeout,
            allow_redirects=allow_redirects,
            cookies=cookies,
        )
        if self._response_requires_login(response):
            return None
        response.raise_for_status()
        return response

    def _append_params(self, url: str, params: dict[str, Any] | None) -> str:
        if not params:
            return url
        query = urlencode(
            [(str(key), "" if value is None else str(value)) for key, value in params.items()],
            doseq=True,
        )
        separator = "&" if "?" in url else "?"
        return f"{url}{separator}{query}"

    def _response_requires_login(self, response: Response) -> bool:
        if response.status_code in {401, 403}:
            return True

        location = response.headers.get("Location", "")
        if any(marker in location.lower() for marker in LOGIN_MARKERS):
            return True
        if any(marker in str(response.url).lower() for marker in LOGIN_MARKERS):
            return True

        content_type = response.headers.get("Content-Type", "")
        if "text/html" in content_type.lower():
            body = response.text[:2000].lower()
            if any(marker in body for marker in ["sign in", "servicenow login", "okta", "saml"]):
                return True

        return False

    def _send_request_with_cookies(
        self,
        *,
        method: str,
        url: str,
        headers: dict[str, str],
        payload: str | None,
        timeout: float | None,
        allow_redirects: bool,
        cookies: list[dict[str, Any]],
    ) -> Response:
        jar = self._build_cookie_jar(cookies)
        session = requests.Session()
        try:
            return session.request(
                method=method.upper(),
                url=url,
                headers=headers,
                data=payload,
                timeout=timeout or (self.timeout_ms / 1000),
                allow_redirects=allow_redirects,
                cookies=jar,
            )
        except requests.RequestException as exc:
            raise RequestException(str(exc)) from exc
        finally:
            session.close()

    def _send_request_with_session(
        self,
        *,
        method: str,
        url: str,
        headers: dict[str, str],
        payload: str | None,
        timeout: float | None,
        allow_redirects: bool,
    ) -> Response:
        if self._context is None:
            raise RequestException("Browser context is not available")

        try:
            cookies = self._context.cookies()
        except Exception as exc:
            raise RequestException(f"Failed to read browser cookies: {exc}") from exc

        return self._send_request_with_cookies(
            method=method,
            url=url,
            headers=headers,
            payload=payload,
            timeout=timeout,
            allow_redirects=allow_redirects,
            cookies=cookies,
        )
