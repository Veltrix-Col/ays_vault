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


COLLECTIVE_BRANCH_CONFIG: dict[str, CollectiveBranch] = {
    "91": CollectiveBranch("91", "salud-colectivo", "Salud colectivo", "Colectivos", ("Salud colectivo",), "people_group", "person"),
    "86": CollectiveBranch("86", "exequial-colectivo", "Exequial colectivo", "Colectivos", ("Exequial colectivo",), "family_group", "person", special_rules=("El codigo 86 tambien existe para Exequial individual; exigir valor Zoho exacto.",)),
    "28": CollectiveBranch("28", "hogar-colectivo", "Hogar colectivo", "Colectivos", ("Hogar colectivo",), "property", "property"),
    "83": CollectiveBranch(
        "83", "vida-grupo-deudores", "Vida grupo deudores", "Colectivos",
        (
            "VG deudores", "Vida grupo deudores", "VG voluntario", "VG voluntaria",
            "VG flexibilización", "VG legal", "VG mixto", "VG patronal",
        ),
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
    for value in (
        "VG deudores", "Vida grupo deudores", "VG voluntario", "VG voluntaria",
        "VG flexibilización", "VG legal", "VG mixto", "VG patronal",
    )
}


validate_branch_config()
