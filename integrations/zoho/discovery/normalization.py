from __future__ import annotations

from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import Any, Iterable, Mapping

from .constants import FIELD_KEYS, MODULE_KEYS, SECRET_KEY_PARTS


def _mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    if is_dataclass(value):
        return asdict(value)
    result: dict[str, Any] = {}
    for key in set(MODULE_KEYS + FIELD_KEYS + (
        "name", "display_value", "actual_value", "sequence_number", "active",
        "sections", "profiles", "fields", "href", "type", "module", "related_module",
        "environment", "organization_id", "company_name", "country", "currency",
        "timezone", "data_center",
    )):
        if hasattr(value, key):
            result[key] = getattr(value, key)
    return result


def _secret_key(key: str) -> bool:
    normalized = key.casefold().replace("-", "_")
    return any(part in normalized for part in SECRET_KEY_PARTS)


def safe_value(value: Any, *, depth: int = 0) -> Any:
    """Convert metadata into deterministic JSON-safe values and drop secrets."""

    if depth > 8:
        return None
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return safe_value(value.value, depth=depth + 1)
    getter = getattr(value, "get_value", None)
    if callable(getter):
        try:
            return safe_value(getter(), depth=depth + 1)
        except Exception:
            return None
    if isinstance(value, Mapping) or is_dataclass(value):
        source = _mapping(value)
        return {
            str(key): safe_value(item, depth=depth + 1)
            for key, item in sorted(source.items(), key=lambda pair: str(pair[0]).casefold())
            if not _secret_key(str(key))
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [safe_value(item, depth=depth + 1) for item in value]
    # Never serialize arbitrary repr() output: third-party objects may embed
    # request bodies, headers or credentials in their representation.
    return None


def _first(mapping: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return default


def normalize_module(value: Any) -> dict[str, Any]:
    raw = _mapping(value)
    result = {
        key: safe_value(raw[key])
        for key in MODULE_KEYS
        if key in raw and raw[key] is not None
    }
    result["api_name"] = str(_first(raw, "api_name", "module_name", default=""))
    result["module_name"] = str(_first(raw, "module_name", "api_name", default=""))
    result["label"] = str(_first(raw, "label", "plural_label", "module_name", default=""))
    return result


def normalize_field(module: Mapping[str, Any], value: Any) -> dict[str, Any]:
    raw = _mapping(value)
    result = {
        key: safe_value(raw[key])
        for key in FIELD_KEYS
        if key in raw and raw[key] is not None
    }
    result.update({
        "module_api_name": module.get("api_name", ""),
        "module_id": module.get("id", ""),
        "field_id": _first(raw, "field_id", "id", default=""),
        "field_label": str(_first(raw, "field_label", "display_label", default="")),
        "api_name": str(_first(raw, "api_name", default="")),
        "data_type": str(_first(raw, "data_type", default="")),
    })
    return result


def _nested(mapping: Mapping[str, Any], *paths: tuple[str, ...]) -> Any:
    for path in paths:
        current: Any = mapping
        for key in path:
            if not isinstance(current, Mapping) or key not in current:
                current = None
                break
            current = current[key]
        if current not in (None, "", {}):
            return current
    return None


def relationship_from_field(field: Mapping[str, Any]) -> dict[str, Any] | None:
    lookup = field.get("lookup") if isinstance(field.get("lookup"), Mapping) else {}
    related = (
        field.get("related_details")
        if isinstance(field.get("related_details"), Mapping)
        else {}
    )
    data_type = str(field.get("data_type") or "").casefold()
    if not lookup and not related and data_type not in {"lookup", "ownerlookup", "userlookup"}:
        return None
    target_api = _nested(
        lookup,
        ("module", "api_name"), ("module", "module_name"),
        ("api_name",), ("module_api_name",),
    ) or _nested(
        related,
        ("module", "api_name"), ("module", "module_name"),
        ("api_name",), ("module_api_name",),
    )
    target_id = _nested(lookup, ("module", "id"), ("module_id",)) or _nested(
        related, ("module", "id"), ("module_id",)
    )
    related_list = _nested(
        related, ("api_name",), ("related_list",), ("related_list_name",)
    )
    lookup_id = _nested(lookup, ("id",), ("lookup_id",))
    resolved = bool(target_api or target_id)
    return {
        "source_module": field.get("module_api_name", ""),
        "source_module_id": field.get("module_id", ""),
        "source_field": field.get("field_label", ""),
        "source_field_api_name": field.get("api_name", ""),
        "source_field_id": field.get("field_id", ""),
        "relationship_type": "lookup",
        "target_module": str(target_api or ""),
        "target_module_api_name": str(target_api or ""),
        "target_module_id": str(target_id or ""),
        "related_list": str(related_list or ""),
        "lookup_id": str(lookup_id or ""),
        "resolved": resolved,
        "reason": "" if resolved else "metadata_target_missing",
    }


def picklists_from_field(field: Mapping[str, Any]) -> list[dict[str, Any]]:
    values = field.get("pick_list_values", field.get("picklist_values", []))
    if not isinstance(values, Iterable) or isinstance(values, (str, bytes, Mapping)):
        return []
    result = []
    for position, item in enumerate(values):
        raw = _mapping(item)
        actual = _first(raw, "actual_value", "value", "display_value", default="")
        display = _first(raw, "display_value", "display_label", "actual_value", default=actual)
        result.append({
            "module_api_name": field.get("module_api_name", ""),
            "field_api_name": field.get("api_name", ""),
            "field_id": field.get("field_id", ""),
            "display_value": safe_value(display),
            "actual_value": safe_value(actual),
            "sequence_number": _first(raw, "sequence_number", default=position),
            "active": bool(_first(raw, "active", default=True)),
            "dependency": safe_value(_first(raw, "dependency", "maps", default=None)),
        })
    return result


def normalize_layout(module: Mapping[str, Any], value: Any) -> dict[str, Any]:
    raw = _mapping(value)
    return {
        "module_api_name": module.get("api_name", ""),
        "module_id": module.get("id", ""),
        "layout_id": str(_first(raw, "layout_id", "id", default="")),
        "layout_name": str(_first(raw, "layout_name", "name", default="")),
        "status": safe_value(_first(raw, "status", default="")),
        "visible": safe_value(_first(raw, "visible", default=None)),
        "sections": safe_value(_first(raw, "sections", default=[])),
        "fields": safe_value(_first(raw, "fields", default=[])),
        "profiles": safe_value(_first(raw, "profiles", default=[])),
    }


def normalize_related_list(module: Mapping[str, Any], value: Any) -> dict[str, Any]:
    raw = _mapping(value)
    related_module = _first(raw, "related_module", "module", default={})
    related_map = _mapping(related_module)
    return {
        "source_module": module.get("api_name", ""),
        "source_module_id": module.get("id", ""),
        "related_list_name": str(_first(raw, "related_list_name", "name", "label", default="")),
        "related_module": str(_first(related_map, "api_name", "module_name", default="")),
        "related_module_id": str(_first(related_map, "id", default="")),
        "api_name": str(_first(raw, "api_name", default="")),
        "href": str(_first(raw, "href", default="")),
        "type": str(_first(raw, "type", default="")),
        "sequence_number": _first(raw, "sequence_number", default=None),
        "visible": safe_value(_first(raw, "visible", default=None)),
    }
