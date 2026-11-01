"""Typed reads from a decoded JSON object, tolerant of fields the server leaves out."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any


def string(data: Mapping[str, Any], key: str, default: str = "") -> str:
    value = opt_string(data, key)
    return default if value is None else value


def opt_string(data: Mapping[str, Any], key: str) -> str | None:
    value = data.get(key)
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return None


def integer(data: Mapping[str, Any], key: str, default: int = 0) -> int:
    value = opt_int(data, key)
    return default if value is None else value


def opt_int(data: Mapping[str, Any], key: str) -> int | None:
    value = data.get(key)
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None


def boolean(data: Mapping[str, Any], key: str, default: bool = False) -> bool:
    value = data.get(key)
    return value if isinstance(value, bool) else default


def obj(data: Mapping[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key)
    return {str(name): item for name, item in value.items()} if isinstance(value, Mapping) else {}


def objects(data: Mapping[str, Any], key: str) -> list[dict[str, Any]]:
    value = data.get(key)
    if not isinstance(value, list):
        return []
    return [{str(name): item for name, item in entry.items()} for entry in value if isinstance(entry, Mapping)]


def counters(data: Mapping[str, Any], key: str) -> dict[str, int | str]:
    """Numbers and words (``'unlimited'``) of a counter map, such as ``remaining`` or ``limits``."""
    result: dict[str, int | str] = {}
    for name, value in obj(data, key).items():
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, str)):
            result[name] = value
        elif isinstance(value, float):
            result[name] = int(value)
    return result


def moment(data: Mapping[str, Any], key: str) -> datetime | None:
    """An ISO 8601 time as an aware datetime (UTC when the text gives no offset), or None."""
    return parse_time(data.get(key))


def parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
