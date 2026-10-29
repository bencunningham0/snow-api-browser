from __future__ import annotations

from typing import Any

from requests import Response

from .browser_auth import BrowserSessionClient
from .config import BrowserAuthConfig
from .query import and_query, escape_query_value
from .resources import (
    ActivityResource,
    EmailResource,
    TableResource,
    TasksResource,
    TicketResource,
    UsersResource,
)


class ServiceNowClient:
    def __init__(
        self,
        *,
        instance_url: str,
        browser: BrowserAuthConfig | None = None,
        timeout: int = 30,
    ) -> None:
        self.instance_url = instance_url.rstrip("/")
        self.timeout = timeout
        self.browser = browser or BrowserAuthConfig()
        self._browser_client = BrowserSessionClient(
            self.instance_url, self.browser, timeout_seconds=timeout
        )
        self.users = UsersResource(self)
        self.tasks = TasksResource(self)
        self.incidents = TicketResource(self, "incident")
        self.changes = TicketResource(self, "change_request")
        self.request_items = TicketResource(self, "sc_req_item")
        self.activity = ActivityResource(self)
        self.email = EmailResource(self)

    def close(self) -> None:
        self._browser_client.close()

    def ensure_authenticated(self) -> dict[str, str | None]:
        return self._browser_client.ensure_authenticated()

    def table(self, name: str) -> TableResource:
        return TableResource(self, name)

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        data: Any = None,
        headers: dict[str, str] | None = None,
    ) -> Response:
        url = path if path.startswith(("http://", "https://")) else f"{self.instance_url}{path}"
        return self._browser_client.request(
            method,
            url,
            params=params,
            json_body=json_body,
            data=data,
            headers=headers,
            timeout=self.timeout,
        )

    def query_table(
        self,
        table: str,
        *,
        filters: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        sysparm_query: str | None = None,
    ) -> list[dict[str, Any]]:
        query_params = {
            "sysparm_display_value": "true",
            **(params or {}),
        }
        query_parts: list[str] = []
        if sysparm_query:
            query_parts.append(sysparm_query)
        if filters:
            query_parts.extend(
                f"{key}={escape_query_value(value)}" for key, value in filters.items()
            )
        combined_query = and_query(query_parts)
        if combined_query:
            query_params["sysparm_query"] = combined_query

        response = self.request("GET", f"/api/now/table/{table}", params=query_params)
        return response.json().get("result", [])

    def get_record(
        self,
        table: str,
        sys_id: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = self.request("GET", f"/api/now/table/{table}/{sys_id}", params=params)
        return response.json().get("result", {})

    def update_record(self, table: str, sys_id: str, data: dict[str, Any]) -> dict[str, Any]:
        response = self.request("PUT", f"/api/now/table/{table}/{sys_id}", json_body=data)
        return response.json().get("result", {})

    def resolve_record(
        self,
        table: str,
        *,
        sys_id: str | None = None,
        number: str | None = None,
    ) -> dict[str, Any]:
        if sys_id:
            return self.get_record(table, sys_id)
        if not number:
            raise ValueError("Either sys_id or number is required")
        records = self.query_table(table, filters={"number": number}, params={"sysparm_limit": 1})
        if not records:
            raise LookupError(f"Record not found in {table}: {number}")
        return records[0]
