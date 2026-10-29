from __future__ import annotations

from dataclasses import dataclass


DEFAULT_PROFILE_PATH = ".playwright-profile"
DEFAULT_LOGIN_PATH = "/now/nav/ui/classic/params/target/home.do"
DEFAULT_LOGIN_TIMEOUT_SECONDS = 600


@dataclass(slots=True)
class BrowserAuthConfig:
    profile_path: str = DEFAULT_PROFILE_PATH
    headless: bool = True
    login_path: str = DEFAULT_LOGIN_PATH
    transaction_source: str = "snow-api-browser"
    interactive_login_timeout_seconds: int = DEFAULT_LOGIN_TIMEOUT_SECONDS
