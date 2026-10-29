from __future__ import annotations

from collections.abc import Iterable


def escape_query_value(value: object) -> str:
    return str(value).replace("^", "^^")


def and_query(parts: Iterable[str | None]) -> str:
    return "^".join(part for part in parts if part)


def or_like(fields: Iterable[str], keyword: str) -> str:
    escaped = escape_query_value(keyword)
    return "^OR".join(f"{field}LIKE{escaped}" for field in fields)
