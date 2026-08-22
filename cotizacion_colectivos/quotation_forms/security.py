from __future__ import annotations

import json

from django.conf import settings
from django.core import signing

from vault.crypto import decrypt, encrypt


CONTEXT_SALT = "cotizacion_colectivos.individual.context.v1"
RECEIPT_SALT = "cotizacion_colectivos.individual.receipt.v1"
POLICY_CONTEXT_SALT = "cotizacion_colectivos.individual.policy-context.v1"


def sign_context(*, entity_kind: str, entity_token: str, label: str) -> str:
    if entity_kind not in {"company", "person"}:
        raise signing.BadSignature("context")
    protected = encrypt(json.dumps({
        "entity_kind": entity_kind,
        "entity_token": str(entity_token)[:1200],
        "label": str(label).strip()[:180],
    }, ensure_ascii=False, sort_keys=True))
    return signing.dumps({"protected": protected}, salt=CONTEXT_SALT, compress=True)


def unsign_context(token: str) -> dict[str, str]:
    try:
        envelope = signing.loads(token, salt=CONTEXT_SALT, max_age=7 * 86400)
        payload = json.loads(decrypt(envelope["protected"]))
    except (signing.BadSignature, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise signing.BadSignature("El contexto no es válido.") from exc
    if payload.get("entity_kind") not in {"company", "person"} or not payload.get("entity_token"):
        raise signing.BadSignature("El contexto no es válido.")
    return payload


def sign_policy_context(payload: dict[str, object]) -> str:
    required = {"policy_token", "source_kind", "affiliate_key", "branch_slug", "schema_version"}
    if not required.issubset(payload) or payload.get("source_kind") not in {"company", "person"}:
        raise signing.BadSignature("El contexto no es válido.")
    protected = encrypt(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return signing.dumps({"protected": protected}, salt=POLICY_CONTEXT_SALT, compress=True)


def unsign_policy_context(token: str) -> dict[str, object]:
    try:
        envelope = signing.loads(
            token,
            salt=POLICY_CONTEXT_SALT,
            max_age=settings.COLECTIVOS_EXTERNAL_LINK_TTL_SECONDS,
        )
        payload = json.loads(decrypt(envelope["protected"]))
    except (signing.BadSignature, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise signing.BadSignature("El enlace no es válido o ya venció.") from exc
    required = {"policy_token", "source_kind", "affiliate_key", "branch_slug", "schema_version"}
    if not required.issubset(payload) or payload.get("source_kind") not in {"company", "person"}:
        raise signing.BadSignature("El enlace no es válido.")
    return payload


def sign_receipt(public_id) -> str:
    return signing.dumps({"quotation": str(public_id)}, salt=RECEIPT_SALT)


def unsign_receipt(token: str) -> str:
    try:
        payload = signing.loads(token, salt=RECEIPT_SALT, max_age=86400)
        return str(payload["quotation"])
    except (signing.BadSignature, KeyError, TypeError) as exc:
        raise signing.BadSignature("La confirmación no es válida.") from exc
