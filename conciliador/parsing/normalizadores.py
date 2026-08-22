"""Funciones puras de normalizacion y parsing de texto/numeros.

Sin estado, sin I/O: son las mas faciles de testear del paquete y las que
mas se reutilizan entre `sources`.
"""

from __future__ import annotations

import re
import unicodedata

import pandas as pd


def strip_accents(texto: str) -> str:
    if not isinstance(texto, str):
        return texto
    return "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c))


def normalize_name(valor) -> str:
    if pd.isna(valor):
        return ""
    return strip_accents(str(valor)).strip().upper()


def normalize_plate(valor) -> str:
    if pd.isna(valor):
        return ""
    return str(valor).strip().upper()


def normalize_doc(valor) -> str:
    """Extrae solo los digitos de un documento (quita prefijos tipo 'C', 'A', ceros a la izquierda)."""
    if pd.isna(valor):
        return ""
    digitos = re.sub(r"\D", "", str(valor))
    return digitos.lstrip("0") or "0"


def parse_cop(valor) -> float:
    """Parsea montos tipo 'CO$ 167,171.00' -> 167171.0"""
    if pd.isna(valor):
        return 0.0
    if isinstance(valor, (int, float)):
        return float(valor)
    texto = re.sub(r"[^\d,.\-]", "", str(valor))
    if not texto:
        return 0.0
    texto = texto.replace(",", "")
    try:
        return float(texto)
    except ValueError:
        return 0.0


def parse_miles_punto(valor) -> float:
    """Parsea numeros de texto donde '.' se usa como separador de miles
    (ej. '167.171' -> 167171.0), salvo que el ultimo grupo no tenga 3 digitos,
    en cuyo caso se asume separador decimal (ej. '167.5' -> 167.5)."""
    if valor is None:
        return 0.0
    texto = str(valor).strip()
    if not texto:
        return 0.0
    if "." in texto:
        _, cola = texto.rsplit(".", 1)
        if len(cola) == 3 and cola.isdigit():
            texto = texto.replace(".", "")
    try:
        return float(texto)
    except ValueError:
        return 0.0
