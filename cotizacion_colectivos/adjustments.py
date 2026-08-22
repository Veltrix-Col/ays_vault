from __future__ import annotations

from dataclasses import dataclass


ADJUSTMENT_CATALOG_VERSION = 1


@dataclass(frozen=True)
class AdjustmentType:
    code: str
    label: str
    implemented: bool = False


ADJUSTMENT_CATALOG = {
    "SIN_CAMBIOS": AdjustmentType("SIN_CAMBIOS", "Sin cambios", True),
    "INGRESO": AdjustmentType("INGRESO", "Ingreso"),
    "INCLUSION": AdjustmentType("INCLUSION", "Ingreso", True),
    "RETIRO": AdjustmentType("RETIRO", "Retiro", True),
    "EXCLUSION": AdjustmentType("EXCLUSION", "Exclusión"),
    "MODIFICACION": AdjustmentType("MODIFICACION", "Modificación"),
    "ACTUALIZACION_DATOS": AdjustmentType("ACTUALIZACION_DATOS", "Actualización de datos"),
    "CAMBIO_PLAN": AdjustmentType("CAMBIO_PLAN", "Cambio de plan"),
    "CAMBIO_BENEFICIARIO": AdjustmentType("CAMBIO_BENEFICIARIO", "Cambio de beneficiario"),
    "INCLUSION_BENEFICIARIO": AdjustmentType("INCLUSION_BENEFICIARIO", "Inclusión de beneficiario"),
    "RETIRO_BENEFICIARIO": AdjustmentType("RETIRO_BENEFICIARIO", "Retiro de beneficiario"),
    "CAMBIO_RIESGO": AdjustmentType("CAMBIO_RIESGO", "Cambio de riesgo"),
}

# Solo se habilitan operaciones que el modelo de respuesta actual puede representar.
_IMPLEMENTED = ("SIN_CAMBIOS", "INCLUSION", "RETIRO")
BRANCH_ADJUSTMENTS = {code: _IMPLEMENTED for code in ("91", "86", "28", "83", "40")}


def allowed_adjustments(branch_code: str) -> tuple[AdjustmentType, ...]:
    return tuple(ADJUSTMENT_CATALOG[code] for code in BRANCH_ADJUSTMENTS.get(branch_code, ()))


def validate_adjustment_codes(branch_code: str, values) -> tuple[str, ...]:
    normalized = tuple(dict.fromkeys(str(value).strip().upper() for value in values if str(value).strip()))
    allowed = set(BRANCH_ADJUSTMENTS.get(branch_code, ()))
    if not normalized or any(value not in allowed for value in normalized):
        raise ValueError("La selección contiene ajustes no permitidos para el ramo.")
    # Mantener el estado actual no es una novedad elegible: siempre debe ser
    # posible para cada fila aunque el operador solo solicite inclusiones/retiros.
    return tuple(dict.fromkeys(("SIN_CAMBIOS", *normalized)))
