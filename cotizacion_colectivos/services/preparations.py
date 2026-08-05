from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict
from datetime import datetime, timedelta

from django.conf import settings
from django.core.cache import cache
from django.core.signing import salted_hmac
from django.db import transaction
from django.utils import timezone

from vault.crypto import decrypt, encrypt

from ..dto import ContactSummary, GroupMember, PolicyDetail, RelatedInsured, RelatedRisk
from ..models import WorkspacePolizaColectivo
from .common import ColectivosServiceError, unsign_record_context
from .functional_groups import consolidate_functional_groups


PREPARATION_VERSION = 1
PARAMETER_VERSION = 1


def _workspace_ttl() -> int:
    return getattr(
        settings,
        "COLECTIVOS_POLICY_WORKSPACE_TTL_SECONDS",
        settings.COLECTIVOS_POLICY_PREPARATION_TTL_SECONDS,
    )


def _identity(*, token: str, profile: str, backend: str, source_kind: str | None) -> tuple[str, dict[str, str]]:
    context = unsign_record_context(token, "policy")
    normalized_kind = source_kind or context.get("source_kind") or "company"
    material = ":".join((
        profile, backend.strip().lower(), normalized_kind,
        context["id"], str(context.get("source_id") or ""),
    ))
    digest = salted_hmac("colectivos.policy.preparation.v1", material).hexdigest()
    return f"colectivos:policy-preparation:v1:{digest}", context


def _checksum(payload: dict[str, object]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _detail_from_dict(value: dict[str, object], *, token: str) -> PolicyDetail:
    data = dict(value)
    # A signed capability belongs to the current HTTP navigation, not to the
    # persisted business snapshot. Older snapshots may also contain the repr
    # of an SDK layout object; never render that implementation detail.
    data["detail_token"] = token
    for field in ("layout_name", "layout_category"):
        text = str(data.get(field) or "").strip()
        if text.startswith("<") or " object at 0x" in text.casefold():
            text = ""
        data[field] = text
    data["payment_calendar"] = tuple(tuple(item) for item in data.get("payment_calendar", ()))
    data["insured"] = tuple(RelatedInsured(**item) for item in data.get("insured", ()))
    data["risks"] = tuple(
        RelatedRisk(**{**item, "attributes": tuple(tuple(pair) for pair in item.get("attributes", ()))})
        for item in data.get("risks", ())
    )
    data["warnings"] = tuple(data.get("warnings", ()))
    data["plan_values"] = tuple(data.get("plan_values", ()))
    data["economic_values"] = tuple(
        tuple(item) for item in data.get("economic_values", ())
    )
    if isinstance(data.get("source_summary"), dict):
        data["source_summary"] = ContactSummary(**data["source_summary"])
    return PolicyDetail(**data)


def _member_from_dict(value: dict[str, object]) -> GroupMember:
    data = dict(value)
    data["risk_attributes"] = tuple(tuple(item) for item in data.get("risk_attributes", ()))
    data["economic_values"] = tuple(tuple(item) for item in data.get("economic_values", ()))
    return GroupMember(**data)


def _functional_groups(members: tuple[GroupMember, ...], branch_code: str):
    rows = []
    for index, member in enumerate(members):
        material = ":".join((
            member.associate_key, member.insured_key, member.beneficiary_key,
            member.risk_key, member.role, str(index),
        ))
        rows.append({
            "public_key": salted_hmac("colectivos.workspace.row.v1", material).hexdigest(),
            "role": member.role,
            "display_name": member.display_name,
            "id_type": member.id_type,
            "masked_document": member.masked_document,
            "initial_status": member.state,
            "plan": member.plan,
            "relationship": member.relationship,
            "entry_date": member.entry_date,
            "exit_date": member.exit_date,
            "risk_key": member.risk_key,
            "risk_summary": member.risk_summary,
            "risk_attributes": dict(member.risk_attributes),
            "economic_values": dict(member.economic_values),
            "associate_key": member.associate_key,
            "associate_name": member.associate_name,
            "associate_id_type": member.associate_id_type,
            "associate_masked_document": member.associate_masked_document,
            "insured_key": member.insured_key,
            "insured_name": member.insured_name,
            "insured_id_type": member.insured_id_type,
            "insured_masked_document": member.insured_masked_document,
            "beneficiary_key": member.beneficiary_key,
            "beneficiary_name": member.beneficiary_name,
            "beneficiary_id_type": member.beneficiary_id_type,
            "beneficiary_masked_document": member.beneficiary_masked_document,
        })
    return consolidate_functional_groups(tuple(rows), branch_code=branch_code)


def _safe_metrics(timings: dict[str, int] | None) -> dict[str, int]:
    return {
        str(key): int(value)
        for key, value in (timings or {}).items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    }


def store_policy_preparation(
    *, token: str, profile: str, backend: str, source_kind: str | None,
    detail: PolicyDetail, members: tuple[GroupMember, ...], actor: str = "technical",
    timings: dict[str, int] | None = None,
) -> dict[str, object]:
    key, context = _identity(
        token=token, profile=profile, backend=backend, source_kind=source_kind,
    )
    now = timezone.now()
    ttl = _workspace_ttl()
    functional_groups, grouping_warnings = _functional_groups(members, detail.branch_code)
    payload = {
        "version": PREPARATION_VERSION,
        "parameter_version": PARAMETER_VERSION,
        "profile": profile,
        "backend": backend.strip().lower(),
        "source_kind": source_kind or context.get("source_kind") or "company",
        "policy_reference": salted_hmac(
            "colectivos.policy.reference.v1", context["id"]
        ).hexdigest(),
        "branch_code": detail.branch_code,
        "queried_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=ttl)).isoformat(),
        "actor": actor,
        "detail": asdict(detail),
        "members": [asdict(member) for member in members],
        "functional_groups": functional_groups,
        "grouping_warnings": grouping_warnings,
    }
    serialization_started = time.monotonic()
    envelope = {"payload": payload, "checksum": _checksum(payload)}
    serialized = json.dumps(envelope, ensure_ascii=False, sort_keys=True)
    if timings is not None:
        timings["snapshot_serialization_ms"] = round(
            (time.monotonic() - serialization_started) * 1000
        )
    encryption_started = time.monotonic()
    protected = encrypt(serialized)
    if timings is not None:
        timings["encryption_ms"] = round((time.monotonic() - encryption_started) * 1000)
    workspace_key = key.rsplit(":", 1)[-1]
    safe_metrics = _safe_metrics(timings)
    event = {
        "type": "ZOHO_REFRESH" if actor == "manual_refresh" else "ZOHO_INITIAL_LOAD",
        "at": now.isoformat(),
        "duration_ms": safe_metrics.get("total_ms", 0),
        "remote_queries": safe_metrics.get("remote_queries", 0),
    }
    policy_hash = salted_hmac(
        "colectivos.workspace.policy.v1", context["id"]
    ).hexdigest()
    source_hash = salted_hmac(
        "colectivos.workspace.source.v1",
        str(context.get("source_id") or f"{payload['source_kind']}:{context['id']}"),
    ).hexdigest()
    database_started = time.monotonic()
    with transaction.atomic():
        current = WorkspacePolizaColectivo.objects.select_for_update().filter(
            workspace_key=workspace_key,
        ).first()
        timeline = list(current.safe_timeline if current else ())[-49:]
        timeline.append(event)
        values = {
            "profile": profile,
            "backend": backend.strip().lower(),
            "source_kind": payload["source_kind"],
            "policy_reference_hash": policy_hash,
            "source_reference_hash": source_hash,
            "encrypted_snapshot": protected,
            "snapshot_checksum": envelope["checksum"],
            "snapshot_version": PREPARATION_VERSION,
            "revision": (current.revision + 1) if current else 1,
            "record_count": len(members),
            "warning_count": len(detail.warnings) + len(grouping_warnings),
            "safe_metrics": safe_metrics,
            "safe_timeline": timeline,
            "synced_at": now,
            "expires_at": now + timedelta(seconds=ttl),
        }
        if current:
            for field, value in values.items():
                setattr(current, field, value)
            current.save(update_fields=(*values.keys(), "updated_at"))
            workspace = current
        else:
            workspace = WorkspacePolizaColectivo.objects.create(
                workspace_key=workspace_key, **values,
            )
    if timings is not None:
        timings["workspace_persistence_ms"] = round(
            (time.monotonic() - database_started) * 1000
        )
    cache.set(key, protected, ttl)
    return {
        "status": "miss", "queried_at": payload["queried_at"],
        "queried_at_dt": now, "expires_at": payload["expires_at"],
        "storage": "database", "revision": workspace.revision,
        "workspace_id": workspace.pk, "functional_groups": functional_groups,
        "grouping_warnings": grouping_warnings,
        "safe_metrics": safe_metrics, "safe_timeline": tuple(workspace.safe_timeline),
        "synced_at": workspace.synced_at,
    }


def load_policy_preparation(
    *, token: str, profile: str, backend: str, source_kind: str | None,
    status_out: dict[str, str] | None = None,
) -> tuple[PolicyDetail, tuple[GroupMember, ...], dict[str, object]] | None:
    key, context = _identity(
        token=token, profile=profile, backend=backend, source_kind=source_kind,
    )
    protected = cache.get(key)
    storage = "cache"
    workspace = None
    workspace_key = key.rsplit(":", 1)[-1]
    if protected:
        workspace = WorkspacePolizaColectivo.objects.filter(
            workspace_key=workspace_key,
            profile=profile,
            backend=backend.strip().lower(),
        ).first()
    if not protected:
        storage = "database"
        workspace = WorkspacePolizaColectivo.objects.filter(
            workspace_key=workspace_key,
            profile=profile,
            backend=backend.strip().lower(),
        ).first()
        if workspace is None:
            if status_out is not None:
                status_out["status"] = "miss"
            return None
        if workspace.expires_at <= timezone.now():
            if status_out is not None:
                status_out["status"] = "expired"
            return None
        protected = workspace.encrypted_snapshot
    try:
        envelope = json.loads(decrypt(protected))
        payload = envelope["payload"]
        if envelope.get("checksum") != _checksum(payload):
            raise ValueError("checksum")
        if workspace is not None and workspace.snapshot_checksum != envelope.get("checksum"):
            raise ValueError("stored_checksum")
        if payload.get("version") != PREPARATION_VERSION or payload.get("parameter_version") != PARAMETER_VERSION:
            raise ValueError("version")
        if payload.get("profile") != profile or payload.get("backend") != backend.strip().lower():
            raise ValueError("profile")
        if payload.get("source_kind") != (source_kind or context.get("source_kind") or "company"):
            raise ValueError("source")
        expected_reference = salted_hmac(
            "colectivos.policy.reference.v1", context["id"]
        ).hexdigest()
        if payload.get("policy_reference") != expected_reference:
            raise ValueError("policy")
        if datetime.fromisoformat(payload["expires_at"]) <= timezone.now():
            cache.delete(key)
            if status_out is not None:
                status_out["status"] = "expired"
            return None
        detail = _detail_from_dict(payload["detail"], token=token)
        if detail.branch_code != payload.get("branch_code"):
            raise ValueError("branch")
        members = tuple(_member_from_dict(item) for item in payload.get("members", ()))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        cache.delete(key)
        if status_out is not None:
            status_out["status"] = "invalid"
        return None
    if storage == "database":
        remaining = max(1, int((workspace.expires_at - timezone.now()).total_seconds()))
        cache.set(key, protected, remaining)
    if status_out is not None:
        status_out["status"] = "hit"
    return detail, members, {
        "status": "hit", "queried_at": payload["queried_at"],
        "queried_at_dt": datetime.fromisoformat(payload["queried_at"]),
        "expires_at": payload["expires_at"], "checksum": envelope["checksum"],
        "storage": storage,
        "revision": workspace.revision if workspace else None,
        "workspace_id": workspace.pk if workspace else None,
        "synced_at": workspace.synced_at if workspace else datetime.fromisoformat(payload["queried_at"]),
        "safe_timeline": tuple(workspace.safe_timeline) if workspace else (),
        "safe_metrics": dict(workspace.safe_metrics) if workspace else {},
        "functional_groups": tuple(payload.get("functional_groups", ())),
        "grouping_warnings": tuple(payload.get("grouping_warnings", ())),
    }


def invalidate_policy_preparation(*, token: str, profile: str, backend: str, source_kind: str | None) -> None:
    key, _context = _identity(
        token=token, profile=profile, backend=backend, source_kind=source_kind,
    )
    cache.delete(key)


def preparation_age(queried_at: str) -> int:
    try:
        created = datetime.fromisoformat(queried_at)
    except (TypeError, ValueError):
        raise ColectivosServiceError("invalid_response", "La preparación local no es válida.")
    return max(0, int((timezone.now() - created).total_seconds()))


def _builder_key(*, token: str, profile: str, backend: str, source_kind: str) -> str:
    context = unsign_record_context(token, source_kind)
    material = ":".join((profile, backend.strip().lower(), source_kind, context["id"]))
    digest = salted_hmac("colectivos.builder.preparation.v1", material).hexdigest()
    return f"colectivos:builder-preparation:v1:{digest}"


def store_builder_preparation(
    *, token: str, profile: str, backend: str, source_kind: str,
    client_label: str, policies: tuple[dict[str, object], ...],
) -> None:
    now = timezone.now()
    payload = {
        "version": PREPARATION_VERSION,
        "profile": profile,
        "backend": backend.strip().lower(),
        "source_kind": source_kind,
        "client_label": client_label,
        "policies": policies,
        "expires_at": (
            now + timedelta(seconds=settings.COLECTIVOS_POLICY_PREPARATION_TTL_SECONDS)
        ).isoformat(),
    }
    envelope = {"payload": payload, "checksum": _checksum(payload)}
    cache.set(
        _builder_key(token=token, profile=profile, backend=backend, source_kind=source_kind),
        encrypt(json.dumps(envelope, ensure_ascii=False, sort_keys=True)),
        settings.COLECTIVOS_POLICY_PREPARATION_TTL_SECONDS,
    )


def load_builder_preparation(
    *, token: str, profile: str, backend: str, source_kind: str,
) -> dict[str, object] | None:
    key = _builder_key(token=token, profile=profile, backend=backend, source_kind=source_kind)
    protected = cache.get(key)
    if not protected:
        return None
    try:
        envelope = json.loads(decrypt(protected))
        payload = envelope["payload"]
        if envelope.get("checksum") != _checksum(payload):
            raise ValueError("checksum")
        if (
            payload.get("version") != PREPARATION_VERSION
            or payload.get("profile") != profile
            or payload.get("backend") != backend.strip().lower()
            or payload.get("source_kind") != source_kind
            or datetime.fromisoformat(payload["expires_at"]) <= timezone.now()
            or not isinstance(payload.get("policies"), list)
        ):
            raise ValueError("context")
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        cache.delete(key)
        return None
    return payload
