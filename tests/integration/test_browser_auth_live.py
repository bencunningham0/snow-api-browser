from __future__ import annotations

import os

import pytest
from dotenv import load_dotenv

from snow_api_browser import BrowserAuthConfig, ServiceNowClient
from snow_api_browser.config import DEFAULT_LOGIN_PATH, DEFAULT_PROFILE_PATH


@pytest.mark.integration
def test_lookup_configured_user_live():
    load_dotenv()
    if os.environ.get("SNOW_RUN_INTEGRATION", "").lower() not in {"1", "true", "yes"}:
        pytest.skip("Set SNOW_RUN_INTEGRATION=true to run live integration tests")

    instance_url = os.environ.get("SNOW_INSTANCE_URL")
    username = os.environ.get("SNOW_TEST_USERNAME")
    if not instance_url or not username:
        pytest.skip("Set SNOW_INSTANCE_URL and SNOW_TEST_USERNAME to run live integration tests")

    profile_path = os.environ.get("SNOW_BROWSER_PROFILE", DEFAULT_PROFILE_PATH)
    headless = os.environ.get("SNOW_BROWSER_HEADLESS", "true").lower() != "false"
    login_path = os.environ.get(
        "SNOW_BROWSER_LOGIN_PATH",
        DEFAULT_LOGIN_PATH,
    )

    client = ServiceNowClient(
        instance_url=instance_url,
        browser=BrowserAuthConfig(
            profile_path=profile_path,
            headless=headless,
            login_path=login_path,
        ),
    )

    try:
        user = client.users.get_by_username(username)
    finally:
        client.close()

    assert user["user_name"] == username
