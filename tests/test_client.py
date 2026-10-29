from __future__ import annotations

from snow_api_browser.client import ServiceNowClient
from snow_api_browser.config import BrowserAuthConfig


def test_query_table_builds_sysparm_query(monkeypatch):
    client = ServiceNowClient(
        instance_url="https://example.service-now.com", browser=BrowserAuthConfig()
    )
    captured = {}

    class FakeResponse:
        def json(self):
            return {"result": []}

    def fake_request(method, path, **kwargs):
        captured["method"] = method
        captured["path"] = path
        captured["params"] = kwargs["params"]
        return FakeResponse()

    monkeypatch.setattr(client, "request", fake_request)

    client.query_table("sys_user", filters={"user_name": "test.user"}, params={"sysparm_limit": 1})

    assert captured["method"] == "GET"
    assert captured["path"] == "/api/now/table/sys_user"
    assert captured["params"]["sysparm_limit"] == 1
    assert captured["params"]["sysparm_query"] == "user_name=test.user"


def test_users_get_by_username_returns_first_record(monkeypatch):
    client = ServiceNowClient(
        instance_url="https://example.service-now.com", browser=BrowserAuthConfig()
    )

    def fake_query_table(table, **kwargs):
        assert table == "sys_user"
        assert kwargs["filters"] == {"user_name": "test.user"}
        return [{"sys_id": "123", "user_name": "test.user"}]

    monkeypatch.setattr(client, "query_table", fake_query_table)

    user = client.users.get_by_username("test.user")

    assert user["user_name"] == "test.user"


def test_activity_stream_combines_and_sorts(monkeypatch):
    client = ServiceNowClient(
        instance_url="https://example.service-now.com", browser=BrowserAuthConfig()
    )

    def fake_resolve_record(table, **kwargs):
        assert table == "incident"
        return {"sys_id": "abc", "number": "INC001"}

    def fake_query_table(table, **kwargs):
        if table == "sys_journal_field":
            return [
                {
                    "sys_id": "j1",
                    "element": "comments",
                    "sys_created_on": "2024-01-02 00:00:00",
                    "sys_created_by": "alice",
                    "value": "Hello",
                }
            ]
        if table == "sys_email":
            return [
                {
                    "sys_id": "e1",
                    "sys_created_on": "2024-01-01 00:00:00",
                    "sys_created_by": "mailer",
                    "body": "Email body",
                }
            ]
        raise AssertionError(table)

    monkeypatch.setattr(client, "resolve_record", fake_resolve_record)
    monkeypatch.setattr(client, "query_table", fake_query_table)

    entries = client.activity.get_stream(table="incident", number="INC001")

    assert [entry["type"] for entry in entries] == ["email", "comments"]


def test_ticket_resource_get_by_number(monkeypatch):
    client = ServiceNowClient(
        instance_url="https://example.service-now.com", browser=BrowserAuthConfig()
    )

    monkeypatch.setattr(
        client,
        "resolve_record",
        lambda table, **kwargs: {"sys_id": "abc123", "number": kwargs["number"]},
    )

    record = client.incidents.get(number="INC001")

    assert record["sys_id"] == "abc123"


def test_ticket_resource_uses_supplied_sys_id_directly(monkeypatch):
    client = ServiceNowClient(instance_url="https://example.service-now.com")
    captured = {}

    def fake_get_record(table, sys_id, *, params=None):
        captured.update(table=table, sys_id=sys_id, params=params)
        return {"sys_id": sys_id}

    monkeypatch.setattr(client, "get_record", fake_get_record)
    monkeypatch.setattr(
        client,
        "resolve_record",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected lookup")),
    )

    record = client.incidents.get(sys_id="abc123", params={"sysparm_fields": "number"})

    assert record == {"sys_id": "abc123"}
    assert captured == {
        "table": "incident",
        "sys_id": "abc123",
        "params": {"sysparm_fields": "number"},
    }


def test_email_send_queues_record_and_associates_source(monkeypatch):
    client = ServiceNowClient(
        instance_url="https://example.service-now.com", browser=BrowserAuthConfig()
    )
    captured = []

    class FakeResponse:
        text = """
            <input name="sysparm_ck" value="token123">
            <input name="sysparm_email_id" value="email123">
            <input name="sysparm_configuration_id" value="config123">
            <input name="sysparm_isScanRequired" value="true">
            <input name="sysparm_email_address_filters" value="">
            <input name="sysparm_recipient_lists" value="">
            <input name="MsgReplyTo" value="Service &amp; Support &lt;service@example.com&gt;">
        """

    def fake_request(method, path, **kwargs):
        captured.append((method, path, kwargs))
        return FakeResponse()

    monkeypatch.setattr(client, "request", fake_request)

    email = client.email.send(
        to=["one@example.com", " two@example.com "],
        cc="copy@example.com",
        bcc=["blind@example.com"],
        subject="Test email",
        text="Plain body",
        html="<p>HTML body</p>",
        reply_to="service@example.com",
        table="incident",
        sys_id="incident123",
    )

    assert captured[0] == (
        "GET",
        "/email_client.do",
        {
            "params": {
                "sysparm_table": "incident",
                "sysparm_sys_id": "incident123",
                "sysparm_target": "incident",
                "sys_target": "incident",
                "sys_uniqueValue": "incident123",
                "sysparm_stack": "no",
            },
        },
    )
    assert captured[1][0:2] == ("POST", "/email_client_template.do")
    assert captured[1][2]["data"] == {
        "sysparm_ck": "token123",
        "sysparm_table": "incident",
        "sysparm_sys_id": "incident123",
        "sysparm_email_id": "email123",
        "sysparm_configuration_id": "config123",
        "sysparm_isScanRequired": "true",
        "sysparm_email_address_filters": "",
        "EMAIL-CLIENT": "send",
        "sysparm_recipient_lists": "",
        "canned": "",
        "MsgReplyTo": "service@example.com",
        "MsgTo": "one@example.com,two@example.com",
        "sys_display.to_block": "",
        "input_to": "",
        "MsgCc": "copy@example.com",
        "sys_display.cc_block": "",
        "input_cc": "",
        "MsgBcc": "blind@example.com",
        "sys_display.bcc_block": "",
        "input_bcc": "",
        "subject": "Test email",
        "message.text": "<p>HTML body</p>",
    }
    assert email["sys_id"] == "email123"


def test_email_send_validates_required_fields():
    client = ServiceNowClient(
        instance_url="https://example.service-now.com", browser=BrowserAuthConfig()
    )

    try:
        client.email.send(to=[], subject="Test", table="incident", sys_id="abc", text="Body")
    except ValueError as exc:
        assert str(exc) == "to must contain at least one address"
    else:
        raise AssertionError("Expected empty recipients to fail")

    try:
        client.email.send(
            to="one@example.com", subject="Test", table="incident", sys_id="", text="Body"
        )
    except ValueError as exc:
        assert str(exc) == "table and sys_id are required"
    else:
        raise AssertionError("Expected missing source record to fail")
