"""Serializa un ReporteConciliacion a Excel. Es el unico modulo que sabe
escribir bytes en disco -- el motor y las reglas nunca importan esto, asi
que manana un JsonReportWriter o un ReportRepository en base de datos se
agregan sin tocar la logica de conciliacion."""

from __future__ import annotations

import io
from pathlib import Path

import pandas as pd

from conciliador.domain.models import ReporteConciliacion


def _resumen(reporte: ReporteConciliacion) -> pd.DataFrame:
    conteos = reporte.resumen_por_tipo()
    return pd.DataFrame(
        {"Tipo_Incidente": list(conteos.keys()), "Cantidad": list(conteos.values())}
    )


def reporte_a_bytes(reporte: ReporteConciliacion) -> bytes:
    """Arma el .xlsx en memoria (hojas 'Incidentes' y 'Resumen'). Pensado
    para un backend que devuelve el archivo como respuesta HTTP sin tocar
    el filesystem."""
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        reporte.to_dataframe().to_excel(writer, sheet_name="Incidentes", index=False)
        _resumen(reporte).to_excel(writer, sheet_name="Resumen", index=False)
    return buffer.getvalue()


def escribir_reporte_excel(reporte: ReporteConciliacion, ruta: str | Path) -> Path:
    """Escribe el reporte en disco (uso tipico: CLI / corridas manuales)."""
    ruta = Path(ruta)
    ruta.write_bytes(reporte_a_bytes(reporte))
    return ruta
