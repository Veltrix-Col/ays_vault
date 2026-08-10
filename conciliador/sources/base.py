"""Helpers comunes a todos los adapters de `sources`."""

from __future__ import annotations

import pandas as pd

from conciliador.domain.exceptions import ColumnaFaltanteError


def columna(df: pd.DataFrame, nombre: str, *, archivo: str) -> pd.Series:
    """Devuelve df[nombre] o lanza ColumnaFaltanteError con contexto util
    en vez de dejar pasar el KeyError crudo de pandas."""
    if nombre not in df.columns:
        raise ColumnaFaltanteError(archivo, nombre)
    return df[nombre]


def texto(valor) -> str:
    return str(valor).strip() if pd.notna(valor) else ""
