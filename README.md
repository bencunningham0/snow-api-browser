# snow-api-browser

Browser-authenticated Python SDK for ServiceNow APIs.

## Features

- Reuses an authenticated browser session to call ServiceNow REST APIs
- Automatically validates auth before every SDK call
- Opens a visible browser for sign-in if the saved session is invalid or expired
- Queries users, tasks, incidents, changes, and request items
- Reads normalized ticket activity from journal and email records
- Adds comments and work notes to supported ticket records
- Sends plain-text or HTML email, optionally associated with a ServiceNow record

## Auth behavior

Every SDK request validates the current browser-backed ServiceNow session before sending the API call.

If auth is invalid, the SDK will:

- close any stale browser context
- reopen a visible browser window
- wait for you to complete sign-in
- save cookies to the configured Playwright profile
- retry the original API call with the refreshed session

## Install

PowerShell:

```powershell
python -m venv .venv
& .\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m playwright install chromium
```

Bash:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m playwright install chromium
```

## Configuration

Copy `.env.example` to `.env` and replace the example instance and username:

```dotenv
SNOW_INSTANCE_URL=https://example.service-now.com
SNOW_TEST_USERNAME=example.user
SNOW_RUN_INTEGRATION=false
SNOW_BROWSER_PROFILE=.playwright-profile
SNOW_BROWSER_HEADLESS=true
SNOW_BROWSER_LOGIN_PATH=/now/nav/ui/classic/params/target/home.do
SNOW_BROWSER_LOGIN_TIMEOUT=600
```

`.env` and browser profiles contain local settings or authentication state and are excluded from Git.

## Login

The login command reads `SNOW_INSTANCE_URL` and browser settings from `.env`:

```bash
snow-api-browser-login
```

Values can also be provided explicitly:

```bash
snow-api-browser-login --instance-url=https://example.service-now.com --profile=.playwright-profile
```

## Example

```python
from snow_api_browser import BrowserAuthConfig, ServiceNowClient

client = ServiceNowClient(
    instance_url="https://example.service-now.com",
    browser=BrowserAuthConfig(profile_path=".playwright-profile"),
)

# Any SDK call will validate auth first and prompt browser sign-in if needed.
user = client.users.get_by_username("example.user")
print(user["user_name"])
client.close()
```

## Send email

Emails are queued through ServiceNow's outbound email client and associated with the source record
identified by `table` and `sys_id`:

```python
email = client.email.send(
    to="recipient@example.com",
    subject="Test email",
    text="This is a test email.",
    table="incident",
    sys_id="<incident_sys_id>",
)
print(email["sys_id"])
```

## Tests

```bash
python -m pytest -m "not integration"
```

Live tests are read-only. Set `SNOW_RUN_INTEGRATION=true`, `SNOW_INSTANCE_URL`, and
`SNOW_TEST_USERNAME` in `.env`, then run:

```bash
python -m pytest -m integration
```

Run the quality checks with:

```bash
python -m ruff check .
python -m ruff format --check .
```
