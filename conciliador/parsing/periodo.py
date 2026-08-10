"""Inferencia del periodo (mes/anio) a conciliar."""

from __future__ import annotations

import os
import re
from datetime import datetime

from conciliador.parsing.normalizadores import strip_accents

MESES_ES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}
MESES_NOMBRE = {v: k.capitalize() for k, v in MESES_ES.items() if k != "setiembre"}


def nombre_mes(mes: int) -> str:
    return MESES_NOMBRE.get(mes, str(mes))


def etiqueta_periodo(mes: int, anio: int, separador: str = " ") -> str:
    return f"{nombre_mes(mes)}{separador}{anio}"


def inferir_periodo_desde_nombre_archivo(
    ruta: str, mes: int | None = None, anio: int | None = None
) -> tuple[int, int]:
    """Infiere mes/anio a partir del nombre de archivo (ej. '..._Julio_2026...').
    Usar solo cuando el contenido del archivo no trae el periodo explicito."""
    if mes and anio:
        return mes, anio
    base = strip_accents(os.path.basename(ruta)).lower()
    m_mes = re.search(r"(" + "|".join(MESES_ES.keys()) + r")", base)
    m_anio = re.search(r"(20\d{2})", base)
    mes_final = mes or (MESES_ES[m_mes.group(1)] if m_mes else datetime.now().month)
    anio_final = anio or (int(m_anio.group(1)) if m_anio else datetime.now().year)
    return mes_final, anio_final
