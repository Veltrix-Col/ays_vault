from __future__ import annotations

from dataclasses import dataclass


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
    "83": CollectiveBranch("83", "vida-grupo-deudores", "Vida grupo deudores", "Colectivos", ("Vida grupo deudores",), "debtor_group", "obligation", special_rules=("Obligacion, saldo y entidad acreedora siguen pendientes de API name confirmado.",)),
    "40": CollectiveBranch("40", "movilidad-colectivo", "Movilidad colectivo", "Colectivos", ("Movilidad colectivo",), "vehicle_group", "vehicle", special_rules=("Tratar pagos negativos y estados especiales como advertencias, no como errores.",)),
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
            normalized = value.strip().casefold()
            if not normalized or normalized in values:
                raise BranchConfigurationError("La parametrizacion contiene valores Zoho ambiguos.")
            values.add(normalized)


def classify_branch(value: object) -> CollectiveBranch | None:
    normalized = str(value or "").strip().casefold()
    if not normalized:
        return None
    for branch in COLLECTIVE_BRANCH_CONFIG.values():
        if normalized in {item.casefold() for item in branch.zoho_values}:
            return branch
    return None


validate_branch_config()
