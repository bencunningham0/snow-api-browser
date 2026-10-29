from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from html import escape
from html.parser import HTMLParser
from typing import Any

from .query import and_query, escape_query_value, or_like


@dataclass(slots=True)
class TableResource:
    client: Any
    table: str

    def get(self, sys_id: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.client.get_record(self.table, sys_id, params=params)

    def query(
        self,
        *,
        filters: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        sysparm_query: str | None = None,
    ) -> list[dict[str, Any]]:
        return self.client.query_table(
            self.table,
            filters=filters,
            params=params,
            sysparm_query=sysparm_query,
        )

    def update(self, sys_id: str, data: dict[str, Any]) -> dict[str, Any]:
        return self.client.update_record(self.table, sys_id, data)


@dataclass(slots=True)
class UsersResource:
    client: Any

    def query(
        self,
        *,
        filters: dict[str, Any] | None = None,
        keyword: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        query_parts: list[str] = []
        if filters:
            query_parts.extend(
                f"{key}={escape_query_value(value)}" for key, value in filters.items()
            )
        if keyword:
            query_parts.append(or_like(["name", "user_name", "email"], keyword))
        sysparm_query = and_query(query_parts) or None
        return self.client.query_table("sys_user", params=params, sysparm_query=sysparm_query)

    def get_by_username(self, username: str) -> dict[str, Any]:
        records = self.client.query_table(
            "sys_user",
            filters={"user_name": username},
            params={"sysparm_limit": 1},
        )
        if not records:
            raise LookupError(f"User not found: {username}")
        return records[0]


@dataclass(slots=True)
class TasksResource:
    client: Any

    def query(
        self,
        *,
        keyword: str | None = None,
        filters: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        sysparm_query: str | None = None,
    ) -> list[dict[str, Any]]:
        query_parts: list[str] = []
        if sysparm_query:
            query_parts.append(sysparm_query)
        if filters:
            query_parts.extend(
                f"{key}={escape_query_value(value)}" for key, value in filters.items()
            )
        if keyword:
            query_parts.append(or_like(["number", "short_description", "description"], keyword))
        return self.client.query_table(
            "task", params=params, sysparm_query=and_query(query_parts) or None
        )


@dataclass(slots=True)
class TicketResource:
    client: Any
    table: str

    def get(
        self,
        *,
        sys_id: str | None = None,
        number: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if sys_id:
            return self.client.get_record(self.table, sys_id, params=params)
        record = self.client.resolve_record(self.table, sys_id=sys_id, number=number)
        if params and record.get("sys_id"):
            return self.client.get_record(self.table, record["sys_id"], params=params)
        return record

    def query(
        self,
        *,
        filters: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        sysparm_query: str | None = None,
    ) -> list[dict[str, Any]]:
        return self.client.query_table(
            self.table, filters=filters, params=params, sysparm_query=sysparm_query
        )

    def add_comment(
        self, *, sys_id: str | None = None, number: str | None = None, text: str
    ) -> dict[str, Any]:
        if sys_id:
            return self.client.update_record(self.table, sys_id, {"comments": text})
        record = self.client.resolve_record(self.table, sys_id=sys_id, number=number)
        return self.client.update_record(self.table, record["sys_id"], {"comments": text})

    def add_work_note(
        self, *, sys_id: str | None = None, number: str | None = None, text: str
    ) -> dict[str, Any]:
        if sys_id:
            return self.client.update_record(self.table, sys_id, {"work_notes": text})
        record = self.client.resolve_record(self.table, sys_id=sys_id, number=number)
        return self.client.update_record(self.table, record["sys_id"], {"work_notes": text})


@dataclass(slots=True)
class EmailResource:
    client: Any

    def send(
        self,
        *,
        to: str | Sequence[str],
        subject: str,
        table: str,
        sys_id: str,
        text: str | None = None,
        html: str | None = None,
        cc: str | Sequence[str] | None = None,
        bcc: str | Sequence[str] | None = None,
        reply_to: str | None = None,
    ) -> dict[str, Any]:
        recipients = self._addresses(to, "to")
        if not subject.strip():
            raise ValueError("subject is required")
        if text is None and html is None:
            raise ValueError("Either text or html is required")
        if not table.strip() or not sys_id.strip():
            raise ValueError("table and sys_id are required")

        compose = self.client.request(
            "GET",
            "/email_client.do",
            params={
                "sysparm_table": table,
                "sysparm_sys_id": sys_id,
                "sysparm_target": table,
                "sys_target": table,
                "sys_uniqueValue": sys_id,
                "sysparm_stack": "no",
            },
        )
        parser = _EmailFormParser()
        parser.feed(compose.text)
        required = ("sysparm_ck", "sysparm_email_id", "sysparm_configuration_id")
        missing = [name for name in required if not parser.values.get(name)]
        if missing:
            raise RuntimeError(f"Email compose form is missing: {', '.join(missing)}")

        message = html
        if message is None:
            message = escape(text or "").replace("\n", "<br>")
        form = {
            "sysparm_ck": parser.values["sysparm_ck"],
            "sysparm_table": table,
            "sysparm_sys_id": sys_id,
            "sysparm_email_id": parser.values["sysparm_email_id"],
            "sysparm_configuration_id": parser.values["sysparm_configuration_id"],
            "sysparm_isScanRequired": parser.values.get("sysparm_isScanRequired", "false"),
            "sysparm_email_address_filters": parser.values.get("sysparm_email_address_filters", ""),
            "EMAIL-CLIENT": "send",
            "sysparm_recipient_lists": parser.values.get("sysparm_recipient_lists", ""),
            "canned": "",
            "MsgReplyTo": reply_to or parser.values.get("MsgReplyTo", ""),
            "MsgTo": recipients,
            "sys_display.to_block": "",
            "input_to": "",
            "MsgCc": self._addresses(cc, "cc") if cc is not None else "",
            "sys_display.cc_block": "",
            "input_cc": "",
            "MsgBcc": self._addresses(bcc, "bcc") if bcc is not None else "",
            "sys_display.bcc_block": "",
            "input_bcc": "",
            "subject": subject,
            "message.text": message,
        }
        self.client.request("POST", "/email_client_template.do", data=form)
        return {
            "sys_id": parser.values["sysparm_email_id"],
            "type": "send-ready",
            "recipients": recipients,
            "subject": subject,
            "target_table": table,
            "instance": sys_id,
        }

    @staticmethod
    def _addresses(value: str | Sequence[str], field: str) -> str:
        addresses = [value] if isinstance(value, str) else list(value)
        normalized = [address.strip() for address in addresses if address.strip()]
        if not normalized:
            raise ValueError(f"{field} must contain at least one address")
        return ",".join(normalized)


class _EmailFormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.values: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "input":
            return
        attributes = dict(attrs)
        name = attributes.get("name")
        if name:
            self.values[name] = attributes.get("value") or ""


@dataclass(slots=True)
class ActivityResource:
    client: Any

    def get_stream(
        self,
        *,
        table: str,
        sys_id: str | None = None,
        number: str | None = None,
        include_journal: bool = True,
        include_emails: bool = True,
    ) -> list[dict[str, Any]]:
        record = self.client.resolve_record(table, sys_id=sys_id, number=number)
        entries: list[dict[str, Any]] = []
        if include_journal:
            entries.extend(self._journal_entries(table=table, sys_id=record["sys_id"]))
        if include_emails:
            entries.extend(
                self._email_entries(
                    table=table, sys_id=record["sys_id"], number=record.get("number")
                )
            )
        return sorted(entries, key=lambda item: item.get("created_on") or "")

    def _journal_entries(self, *, table: str, sys_id: str) -> list[dict[str, Any]]:
        records = self.client.query_table(
            "sys_journal_field",
            sysparm_query=and_query(
                [
                    f"name={escape_query_value(table)}",
                    f"element_id={escape_query_value(sys_id)}",
                ]
            ),
            params={"sysparm_display_value": "true", "sysparm_limit": 250},
        )
        return [
            {
                "type": record.get("element") or "journal",
                "created_on": record.get("sys_created_on"),
                "author": record.get("sys_created_by"),
                "body": record.get("value"),
                "source_table": "sys_journal_field",
                "source_sys_id": record.get("sys_id"),
                "raw": record,
            }
            for record in records
        ]

    def _email_entries(
        self, *, table: str, sys_id: str, number: str | None
    ) -> list[dict[str, Any]]:
        query_parts = [f"instance={escape_query_value(sys_id)}"]
        if number:
            query_parts.append(f"ORsubjectLIKE{escape_query_value(number)}")
        records = self.client.query_table(
            "sys_email",
            sysparm_query="".join(query_parts),
            params={"sysparm_display_value": "true", "sysparm_limit": 250},
        )
        return [
            {
                "type": "email",
                "created_on": record.get("sys_created_on") or record.get("created"),
                "author": record.get("user") or record.get("sys_created_by"),
                "body": record.get("body") or record.get("body_text") or record.get("message"),
                "source_table": "sys_email",
                "source_sys_id": record.get("sys_id"),
                "raw": record,
            }
            for record in records
        ]
