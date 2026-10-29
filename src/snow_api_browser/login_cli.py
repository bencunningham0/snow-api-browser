from __future__ import annotations

import argparse
import os
import signal
import time

from dotenv import load_dotenv
from requests.exceptions import RequestException

from .browser_auth import BrowserSessionClient
from .config import (
    DEFAULT_LOGIN_PATH,
    DEFAULT_LOGIN_TIMEOUT_SECONDS,
    DEFAULT_PROFILE_PATH,
    BrowserAuthConfig,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Open a persistent ServiceNow browser session")
    parser.add_argument("--instance-url", default=os.environ.get("SNOW_INSTANCE_URL"))
    parser.add_argument(
        "--profile", default=os.environ.get("SNOW_BROWSER_PROFILE", DEFAULT_PROFILE_PATH)
    )
    parser.add_argument(
        "--login-path",
        default=os.environ.get("SNOW_BROWSER_LOGIN_PATH", DEFAULT_LOGIN_PATH),
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=int(os.environ.get("SNOW_BROWSER_LOGIN_TIMEOUT", DEFAULT_LOGIN_TIMEOUT_SECONDS)),
    )
    parser.add_argument("--stay-open", action="store_true", default=False)
    return parser.parse_args()


def wait_for_authenticated_session(client: BrowserSessionClient, timeout_seconds: int) -> None:
    deadline = time.monotonic() + max(timeout_seconds, 1)
    last_error: str | None = None
    while time.monotonic() < deadline:
        try:
            session = client.ensure_authenticated()
            print(f"Authenticated ServiceNow session detected at {session.get('href')}")
            return
        except RequestException as exc:
            last_error = str(exc)
            time.sleep(2)
        except Exception as exc:
            last_error = str(exc)
            time.sleep(1)
    raise SystemExit(last_error or "Timed out waiting for ServiceNow authentication")


def main() -> None:
    load_dotenv()
    args = parse_args()
    if not args.instance_url:
        raise SystemExit("SNOW_INSTANCE_URL or --instance-url is required")

    client = BrowserSessionClient(
        args.instance_url,
        BrowserAuthConfig(profile_path=args.profile, headless=False, login_path=args.login_path),
    )
    client.start()
    print("Browser session opened. Complete login in the visible browser window.")
    print(f"Profile path: {os.path.abspath(args.profile)}")

    try:
        signal.signal(signal.SIGINT, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    except Exception:
        pass

    try:
        wait_for_authenticated_session(client, args.timeout)
        if args.stay_open:
            print("Session is authenticated. Leaving browser open until you press Ctrl+C.")
            while True:
                time.sleep(1)
        print("Session saved to the persistent browser profile.")
    except KeyboardInterrupt:
        print("Closing browser session.")
    finally:
        client.close()


if __name__ == "__main__":
    main()
