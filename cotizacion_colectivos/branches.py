from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata


class BranchConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class CollectiveBranch:
    code: str
    slug: str
    name: str
    area: str
    zoho_values: tuple[str, ...]
    structure_type: str
    risk_type: str
    supports_updates: bool = True
    supports_renewal: bool = True
    supports_excel: bool = True
    active_statuses: tuple[str, ...] = ("Activo", "Activo con ajuste")
    excluded_statuses: tuple[str, ...] = ("Excluido", "Excluido con cobro")
    special_rules: tuple[str, ...] = ()


# Vida Grupo is one functional family for capture and Contact resolution.
# Write support is tracked explicitly per confirmed Riesgos1 contract so that
# adding a new family value cannot silently enable remote writes.
LIFE_GROUP_VALUES = (
    "VG deudores", "VG patronal", "VG voluntario", "VG legal", "VG mixto",
    "VG flexibilizacion", "VG flexibilización",
)
LIFE_GROUP_CONTRACTS = {
    "VG deudores": {"write_enabled": True, "required_ingress_fields": ("parentesco",)},
    "VG patronal": {"write_enabled": False, "required_ingress_fields": ()},
    "VG voluntario": {"write_enabled": True, "required_ingress_fields": ("parentesco",)},
    "VG legal": {"write_enabled": False, "required_ingress_fields": ()},
    "VG mixto": {"write_enabled": False, "required_ingress_fields": ()},
    "VG flexibilizacion": {"write_enabled": False, "required_ingress_fields": ()},
    "VG flexibilización": {"write_enabled": False, "required_ingress_fields": ()},
}


def contract_required_ingress_fields(branch_code: object = "", branch_name: object = "") -> tuple[str, ...]:
    """Return fields required by the already-confirmed write contract.

    This is deliberately derived from the central branch contracts.  It is
    consumed by both the external form and pre-write validation so the UI
    cannot drift from the builder requirements.
    """
    code = str(branch_code or "").strip().lower()
    ramo = canonical_life_group_value(branch_name)
    if ramo in LIFE_GROUP_CONTRACTS:
        return tuple(LIFE_GROUP_CONTRACTS[ramo].get("required_ingress_fields", ()))
    if code in {"40", "movilidad", "autos"}:
        # build_risk_payload requires a valid plate and model; the remaining
        # vehicle attributes are optional in the confirmed Risk contract.
        return ("plate", "model")
    return ()


def canonical_life_group_value(value: object) -> str:
    """Return the contract key for a Vida Grupo product when it is known.

    Policy snapshots may carry the human branch label (for example
    ``Vida grupo de deudores``) while the Zoho contract is keyed by the
    canonical ``VG deudores`` value.  Keeping this normalization here avoids
    each workflow inventing its own branch matching rules.
    """
    normalized = _normalize_branch_value(value)
    aliases = {
        _normalize_branch_value("Vida grupo de deudores"): "VG deudores",
        _normalize_branch_value("Vida grupo deudores"): "VG deudores",
    }
    if normalized in aliases:
        return aliases[normalized]
    for candidate in LIFE_GROUP_VALUES:
        if _normalize_branch_value(candidate) == normalized:
            return candidate
    return str(value or "").strip()


COLLECTIVE_BRANCH_CONFIG: dict[str, CollectiveBranch] = {
    "91": CollectiveBranch("91", "salud-colectivo", "Salud colectivo", "Colectivos", ("Salud colectivo",), "people_group", "person"),
    "86": CollectiveBranch("86", "exequial-colectivo", "Exequial colectivo", "Colectivos", ("Exequial colectivo",), "family_group", "person", special_rules=("El codigo 86 tambien existe para Exequial individual; exigir valor Zoho exacto.",)),
    "28": CollectiveBranch("28", "hogar-colectivo", "Hogar colectivo", "Colectivos", ("Hogar colectivo",), "property", "property"),
    "83": CollectiveBranch(
        "83", "vida-grupo-deudores", "Vida grupo deudores", "Colectivos",
        # Keep only one accent variant in the Zoho allowlist; the unaccented
        # spelling remains a local alias recognized by the family resolver.
        ("VG deudores", "VG patronal", "VG voluntario", "VG legal", "VG mixto",
         "VG flexibilización", "Vida grupo deudores", "VG voluntaria"),
        "debtor_group", "obligation",
        special_rules=("Zoho usa actualmente el valor de picklist VG deudores.", "Obligacion, saldo y entidad acreedora siguen pendientes de API name confirmado."),
    ),
    "40": CollectiveBranch("40", "movilidad-colectivo", "Movilidad colectivo", "Colectivos", ("Movilidad colectivo",), "vehicle_group", "vehicle", special_rules=("Tratar pagos negativos y estados especiales como advertencias, no como errores.",)),
}

_BRANCH_FAMILY_BY_CODE = {
    "91": "salud",
    "86": "exequial",
    "83": "vida",
    "40": "movilidad",
}


def validate_branch_config(config: dict[str, CollectiveBranch] = COLLECTIVE_BRANCH_CONFIG) -> None:
    codes: set[str] = set()
    slugs: set[str] = set()
    values: set[str] = set()
    for key, branch in config.items():
        if key != branch.code or not branch.code or not branch.name or not branch.slug:
            raise BranchConfigurationError("La parametrizacion de ramos esta incompleta.")
        if branch.code in codes or branch.slug in slugs:
            raise BranchConfigurationError("La parametrizacion contiene codigos o slugs duplicados.")
        codes.add(branch.code)
        slugs.add(branch.slug)
        for value in branch.zoho_values:
            normalized = _normalize_branch_value(value)
            if not normalized or normalized in values:
                raise BranchConfigurationError("La parametrizacion contiene valores Zoho ambiguos.")
            values.add(normalized)


def classify_branch(value: object) -> CollectiveBranch | None:
    normalized = _normalize_branch_value(value)
    if not normalized:
        return None
    for branch in COLLECTIVE_BRANCH_CONFIG.values():
        if normalized in {_normalize_branch_value(item) for item in branch.zoho_values}:
            return branch
    return None


def resolve_branch_family(branch_code: object = "", branch_name: object = "") -> str | None:
    """Return the canonical individual-quotation family for a policy.

    Codes are authoritative when they are known.  Explicit product aliases
    are then consulted so a confirmed Vida Grupo product can use the Vida
    form even when Zoho assigns it a code not present in our collective
    branch catalogue.  Unknown names never fall through to a broad substring
    match.
    """
    code = str(branch_code or "").strip()
    family = _BRANCH_FAMILY_BY_CODE.get(code)
    if family:
        return family
    normalized_name = _normalize_branch_value(branch_name)
    if normalized_name in _BRANCH_FAMILY_ALIASES:
        return _BRANCH_FAMILY_ALIASES[normalized_name]
    if normalized_name == "soat":
        return "soat"
    return None


def _normalize_branch_value(value: object) -> str:
    """Normaliza variaciones tipográficas sin ampliar la allowlist funcional."""
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(character for character in text if not unicodedata.combining(character))
    text = re.sub(r"[\u2010-\u2015]", "-", text)
    return " ".join(text.strip().casefold().split())


_BRANCH_FAMILY_ALIASES = {
    _normalize_branch_value(value): "vida"
    for value in (*LIFE_GROUP_VALUES, "Vida grupo deudores", "VG voluntaria")
}


validate_branch_config()
