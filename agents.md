# Agent Guide

This project exposes a browser-authenticated Python SDK for ServiceNow.

## Auth behavior

All SDK calls automatically validate browser auth before sending a request.

If auth is invalid:
- the SDK closes any stale browser context
- reopens a visible browser window
- waits for the user to complete sign-in
- saves cookies to the configured Playwright profile
- retries the requested API call using the refreshed session

This behavior is implemented in `src/snow_api_browser/browser_auth.py` via `BrowserSessionClient.ensure_authenticated_or_login()` and is used by every `ServiceNowClient.request(...)` call.

## Main entrypoint

Create a client:

```python
from snow_api_browser import BrowserAuthConfig, ServiceNowClient

client = ServiceNowClient(
    instance_url="https://example.service-now.com",
    browser=BrowserAuthConfig(profile_path=".playwright-profile"),
)
```

Close it when finished:

```python
client.close()
```

## Low-level calls

Raw request:

```python
client.request("GET", "/api/now/table/sys_user", params={"sysparm_limit": 1})
```

Generic table access:

```python
client.table("incident").query(filters={"active": "true"})
client.table("incident").get("<sys_id>")
client.table("incident").update("<sys_id>", {"work_notes": "Updated by agent"})
```

## Resource helpers

Users:

```python
client.users.get_by_username("example.user")
client.users.query(filters={"active": "true"}, keyword="laptop")
```

Tasks:

```python
client.tasks.query(keyword="vpn")
client.tasks.query(filters={"number": "TASK0012345"})
client.tasks.query(sysparm_query="priority=1^active=true")
```

`keyword=...` searches `number`, `short_description`, and `description`; it does not search comment history.

Incidents:

```python
client.incidents.query(filters={"state": "2"})
client.incidents.add_comment(number="INC0012345", text="Public comment")
client.incidents.add_work_note(number="INC0012345", text="Internal note")
```

Changes:

```python
client.changes.query(filters={"state": "Assess"})
client.changes.add_comment(number="CHG0012345", text="Change comment")
client.changes.add_work_note(number="CHG0012345", text="Change work note")
```

Request items:

```python
client.request_items.query(filters={"state": "1"})
client.request_items.add_comment(number="RITM0012345", text="Request item comment")
client.request_items.add_work_note(number="RITM0012345", text="Request item work note")
```

Activity stream:

```python
client.activity.get_stream(table="incident", number="INC0012345")
```

Returns a normalized list of entries with:
- `type`
- `created_on`
- `author`
- `body`
- `source_table`
- `source_sys_id`
- `raw`

Note: `client.activity.get_stream(...)` normalizes entries from `sys_journal_field` and `sys_email`.
Some comments visible in ServiceNow, especially imported or externally-synced comments, may instead appear only on record fields such as `comments` or `work_notes`.
For a full ticket review, fetch both the record and the activity stream.

Example:

```python
record = client.incidents.get(
    number="INC0012345",
    params={
        "sysparm_fields": "number,short_description,description,comments,work_notes,state",
    },
)
stream = client.activity.get_stream(table="incident", number="INC0012345")
```

## Query conventions

- Use `filters={...}` for exact field matches.
- Use `keyword=...` on supported resources for LIKE-style search.
- Use `sysparm_query="..."` for advanced encoded queries.
- Use `params={...}` to pass raw `sysparm_*` options such as:
  - `sysparm_limit`
  - `sysparm_offset`
  - `sysparm_fields`
  - `sysparm_display_value`

## Login helper

Manual login helper:

```bash
snow-api-browser-login --instance-url=https://example.service-now.com --profile=.playwright-profile
```

## Constraints

- Prefer read operations unless the user explicitly asks to write.
- For live validation, only perform the user-approved API calls.
- Current integration test coverage is intentionally read-only for the configured `SNOW_TEST_USERNAME`.
