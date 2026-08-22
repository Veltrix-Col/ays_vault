from __future__ import annotations

from collections.abc import Iterable


def summarize_value(value: object) -> str:
    """Describe un valor sin imprimir datos personales ni identificadores."""
    if value is None or value == "":
        return "[vacio]"
    if isinstance(value, bool):
        return "[booleano]"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"[numerico, longitud={len(str(value))}]"
    if isinstance(value, dict):
        return f"[relacion, claves={len(value)}]"
    if isinstance(value, (list, tuple, set)):
        return f"[coleccion, elementos={len(value)}]"
    return f"[texto, longitud={len(str(value))}]"


def summarize_records(
    records: Iterable[dict[str, object]], fields: Iterable[str]
) -> tuple[dict[str, str], ...]:
    selected = tuple(fields)
    return tuple(
        {field: summarize_value(record.get(field)) for field in selected}
        for record in records
    )

