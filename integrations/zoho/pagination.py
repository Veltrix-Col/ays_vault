from __future__ import annotations

from typing import Any

from .schemas import Page


def page_from_response(payload: dict[str, Any], *, key: str = "data") -> Page:
    raw_records = payload.get(key) or []
    records = tuple(item for item in raw_records if isinstance(item, dict))
    info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
    return Page(
        records=records,
        more_records=bool(info.get("more_records")),
        page=int(info.get("page") or 1),
        count=int(info.get("count") or len(records)),
    )
