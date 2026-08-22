from __future__ import annotations

from typing import Any, Callable


def _index(items: list[dict[str, Any]], key: Callable[[dict[str, Any]], tuple]) -> dict[tuple, dict[str, Any]]:
    return {key(item): item for item in items}


def _changes(left: dict[str, Any], right: dict[str, Any], keys: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    return {
        key: {"left": left.get(key), "right": right.get(key)}
        for key in keys
        if left.get(key) != right.get(key)
    }


def _simple_collection(
    left_items: list[dict[str, Any]], right_items: list[dict[str, Any]],
    *, key: Callable[[dict[str, Any]], tuple], compare_keys: tuple[str, ...],
) -> dict[str, Any]:
    left = _index(left_items, key)
    right = _index(right_items, key)
    added = [right[item] for item in sorted(right.keys() - left.keys())]
    removed = [left[item] for item in sorted(left.keys() - right.keys())]
    changed = []
    unchanged = []
    for identity in sorted(left.keys() & right.keys()):
        differences = _changes(left[identity], right[identity], compare_keys)
        if differences:
            changed.append({"identity": list(identity), "changes": differences})
        else:
            unchanged.append(list(identity))
    return {"added": added, "removed": removed, "changed": changed, "unchanged": unchanged}


def _module_capability(snapshot: dict[str, Any], capability: str) -> set[str]:
    unavailable = set()
    for module in snapshot["modules"]:
        api_name = str(module.get("api_name") or "")
        status = module.get(f"{capability}_status")
        if capability == "fields" and status is None:
            status = module.get("fields_metadata_status")
        if str(status or "").casefold() in {"error", "unavailable", "failed"}:
            unavailable.add(api_name)
    return unavailable


def _inconclusive(capability: str, module: str, side: str) -> dict[str, Any]:
    return {
        "change": "comparison_inconclusive",
        "capability": capability,
        "module": module,
        "identity": [module, "*"],
        "reason": f"metadata_unavailable_{side}",
    }


def _field_changes(
    left_fields: list[dict[str, Any]], right_fields: list[dict[str, Any]],
    *, left_unavailable: set[str], right_unavailable: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    key = lambda item: (str(item.get("module_api_name", "")), str(item.get("api_name", "")))
    left = _index(left_fields, key)
    right = _index(right_fields, key)
    result: list[dict[str, Any]] = []
    inconclusive: list[dict[str, Any]] = []
    for identity in sorted(right.keys() - left.keys()):
        if identity[0] in left_unavailable:
            inconclusive.append(_inconclusive("fields", identity[0], "left"))
        else:
            result.append({"change": "field_added", "identity": list(identity), "right": right[identity]})
    for identity in sorted(left.keys() - right.keys()):
        if identity[0] in right_unavailable:
            inconclusive.append(_inconclusive("fields", identity[0], "right"))
        else:
            result.append({"change": "field_removed", "identity": list(identity), "left": left[identity]})
    checks = (
        ("data_type", "field_type_changed"),
        ("required", "field_required_changed"),
        ("system_mandatory", "field_required_changed"),
        ("read_only", "field_read_only_changed"),
        ("field_read_only", "field_read_only_changed"),
        ("lookup", "field_lookup_changed"),
    )
    for identity in sorted(left.keys() & right.keys()):
        if identity[0] in left_unavailable or identity[0] in right_unavailable:
            if identity[0] in left_unavailable:
                inconclusive.append(_inconclusive("fields", identity[0], "left"))
            if identity[0] in right_unavailable:
                inconclusive.append(_inconclusive("fields", identity[0], "right"))
            continue
        emitted = set()
        for attribute, category in checks:
            if left[identity].get(attribute) == right[identity].get(attribute):
                continue
            signature = (category, attribute)
            if signature in emitted:
                continue
            emitted.add(signature)
            result.append({
                "change": category, "identity": list(identity), "attribute": attribute,
                "left": left[identity].get(attribute), "right": right[identity].get(attribute),
            })
    return result, inconclusive


def _suppress_collection(
    comparison: dict[str, Any], *, capability: str,
    left_unavailable: set[str], right_unavailable: set[str],
    module_key: str,
) -> list[dict[str, Any]]:
    inconclusive = []
    kept_added = []
    for item in comparison["added"]:
        module = str(item.get(module_key) or "")
        if module in left_unavailable:
            inconclusive.append(_inconclusive(capability, module, "left"))
        else:
            kept_added.append(item)
    comparison["added"] = kept_added
    kept_removed = []
    for item in comparison["removed"]:
        module = str(item.get(module_key) or "")
        if module in right_unavailable:
            inconclusive.append(_inconclusive(capability, module, "right"))
        else:
            kept_removed.append(item)
    comparison["removed"] = kept_removed
    kept_changed = []
    for item in comparison["changed"]:
        module = str(item["identity"][0])
        unavailable = False
        if module in left_unavailable:
            inconclusive.append(_inconclusive(capability, module, "left"))
            unavailable = True
        if module in right_unavailable:
            inconclusive.append(_inconclusive(capability, module, "right"))
            unavailable = True
        if not unavailable:
            kept_changed.append(item)
    comparison["changed"] = kept_changed
    return inconclusive


def _picklist_changes(left_items: list[dict[str, Any]], right_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    key = lambda item: (
        str(item.get("module_api_name", "")), str(item.get("field_api_name", "")),
        str(item.get("actual_value", "")),
    )
    left = _index(left_items, key)
    right = _index(right_items, key)
    result = []
    for identity in sorted(right.keys() - left.keys()):
        result.append({"change": "value_added", "identity": list(identity), "right": right[identity]})
    for identity in sorted(left.keys() - right.keys()):
        result.append({"change": "value_removed", "identity": list(identity), "left": left[identity]})
    for identity in sorted(left.keys() & right.keys()):
        before = bool(left[identity].get("active", True))
        after = bool(right[identity].get("active", True))
        if before != after:
            result.append({
                "change": "value_enabled" if after else "value_disabled",
                "identity": list(identity), "left": before, "right": after,
            })
        if left[identity].get("display_value") != right[identity].get("display_value"):
            result.append({
                "change": "display_value_changed", "identity": list(identity),
                "left": left[identity].get("display_value"),
                "right": right[identity].get("display_value"),
            })
    return result


def compare_snapshots(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    left_fields_unavailable = _module_capability(left, "fields")
    right_fields_unavailable = _module_capability(right, "fields")
    left_layouts_unavailable = _module_capability(left, "layouts")
    right_layouts_unavailable = _module_capability(right, "layouts")
    modules = _simple_collection(
        left["modules"], right["modules"],
        key=lambda item: (str(item.get("api_name", "")),),
        compare_keys=(
            "id", "module_name", "generated_type", "module_type", "visibility",
            "api_supported", "status", "fields_status", "fields_error_category",
            "fields_metadata_status", "fields_metadata_error", "layouts_status",
            "related_lists_status",
        ),
    )
    layouts = _simple_collection(
        left["layouts"], right["layouts"],
        key=lambda item: (str(item.get("module_api_name", "")), str(item.get("layout_id") or item.get("layout_name", ""))),
        compare_keys=("layout_name", "status", "visible", "sections", "fields", "profiles"),
    )
    relationships = _simple_collection(
        left["relationships"], right["relationships"],
        key=lambda item: (str(item.get("source_module", "")), str(item.get("source_field_api_name", ""))),
        compare_keys=("relationship_type", "target_module_api_name", "target_module_id", "related_list", "lookup_id", "resolved"),
    )
    inconclusive = _suppress_collection(
        layouts, capability="layouts",
        left_unavailable=left_layouts_unavailable,
        right_unavailable=right_layouts_unavailable,
        module_key="module_api_name",
    )
    inconclusive.extend(_suppress_collection(
        relationships, capability="fields",
        left_unavailable=left_fields_unavailable,
        right_unavailable=right_fields_unavailable,
        module_key="source_module",
    ))
    fields, field_inconclusive = _field_changes(
        left["fields"], right["fields"],
        left_unavailable=left_fields_unavailable,
        right_unavailable=right_fields_unavailable,
    )
    inconclusive.extend(field_inconclusive)
    picklists = _picklist_changes(left["picklists"], right["picklists"])
    comparable_picklists = []
    for item in picklists:
        module = str(item["identity"][0])
        sides = []
        if module in left_fields_unavailable:
            sides.append("left")
        if module in right_fields_unavailable:
            sides.append("right")
        if sides:
            inconclusive.extend(_inconclusive("fields", module, side) for side in sides)
        else:
            comparable_picklists.append(item)
    picklists = comparable_picklists
    unique_inconclusive = {
        (item["capability"], item["module"], item["reason"]): item
        for item in inconclusive
    }
    inconclusive = [unique_inconclusive[key] for key in sorted(unique_inconclusive)]
    fields.extend(item for item in inconclusive if item["capability"] == "fields")
    picklist_fields: dict[tuple[str, str], list[str]] = {}
    for item in picklists:
        module, field, _value = item["identity"]
        picklist_fields.setdefault((module, field), []).append(item["change"])
    fields.extend({
        "change": "field_picklist_changed",
        "identity": list(identity),
        "picklist_changes": sorted(categories),
    } for identity, categories in sorted(picklist_fields.items()))
    layout_events = [
        {"change": "layout_added", "layout": item}
        for item in layouts["added"]
    ] + [
        {"change": "layout_removed", "layout": item}
        for item in layouts["removed"]
    ] + [
        {"change": "layout_changed", **item}
        for item in layouts["changed"]
    ]
    relationship_events = [
        {"change": "relationship_added", "relationship": item}
        for item in relationships["added"]
    ] + [
        {"change": "relationship_removed", "relationship": item}
        for item in relationships["removed"]
    ]
    for item in relationships["changed"]:
        relationship_events.append({
            "change": (
                "relationship_target_changed"
                if "target_module_api_name" in item["changes"]
                or "target_module_id" in item["changes"]
                else "relationship_changed"
            ),
            **item,
        })
    critical_categories = {
        "field_removed", "field_type_changed", "field_required_changed",
        "field_read_only_changed", "field_lookup_changed",
    }
    critical = [item for item in fields if item["change"] in critical_categories]
    critical.extend(
        item for item in relationship_events
        if item["change"] in {"relationship_removed", "relationship_target_changed"}
    )
    summary = {
        "modules_added": len(modules["added"]),
        "modules_removed": len(modules["removed"]),
        "modules_changed": len(modules["changed"]),
        "fields_changed": len([item for item in fields if item["change"] != "comparison_inconclusive"]),
        "layouts_added": len(layouts["added"]),
        "layouts_removed": len(layouts["removed"]),
        "layouts_changed": len(layouts["changed"]),
        "relationships_added": len(relationships["added"]),
        "relationships_removed": len(relationships["removed"]),
        "relationships_changed": len(relationships["changed"]),
        "picklists_changed": len(picklists),
        "critical_changes": len(critical),
        "comparisons_inconclusive": len(inconclusive),
    }
    return {
        "schema_version": 2,
        "left_profile": left["manifest"].get("profile"),
        "right_profile": right["manifest"].get("profile"),
        "left_digest": left["manifest"].get("semantic_digest", ""),
        "right_digest": right["manifest"].get("semantic_digest", ""),
        "summary": summary,
        "critical_changes": critical,
        "modules": modules,
        "fields": fields,
        "layouts": {**layouts, "events": layout_events},
        "relationships": {**relationships, "events": relationship_events},
        "picklists": picklists,
        "inconclusive": inconclusive,
        "identical": not any(summary.values()),
    }
