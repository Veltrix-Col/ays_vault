"""Adapter del archivo de cobro de Movilidad: CSV plano de Sharefile, sin
encabezado, separado por ';' y en encoding Windows-1252."""

from __future__ import annotations

import pandas as pd

from conciliador.parsing.normalizadores import normalize_doc, normalize_plate, parse_miles_punto

_COLUMNAS_CSV = ["tipo", "poliza", "id2", "nombre", "documento_raw", "placa",
                  "valor_asegurado", "valor_iva", "valor_total"]


def cargar_cobro_movilidad(ruta) -> pd.DataFrame:
    # dtype=str es clave: si se deja que pandas infiera el tipo, interpreta
    # '167.171' como el float 167.171 (perdiendo el separador de miles) antes
    # de que parse_miles_punto vea el valor original.
    df = pd.read_csv(ruta, sep=";", header=None, names=_COLUMNAS_CSV, encoding="cp1252",
                      engine="python", skip_blank_lines=True, dtype=str)
    df = df.dropna(subset=["placa"])
    out = pd.DataFrame({
        "placa": df["placa"].apply(normalize_plate),
        "documento": df["documento_raw"].apply(normalize_doc),
        "nombre": df["nombre"].astype(str).str.strip(),
        "nombre_titular": "",
        "valor_cobro": df["valor_asegurado"].apply(parse_miles_punto),
        "valor_iva_cobro": df["valor_iva"].apply(parse_miles_punto),
        "valor_total_cobro": df["valor_total"].apply(parse_miles_punto),
    })
    return out[out["placa"] != ""]
