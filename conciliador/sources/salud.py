"""Adapter del archivo de cobro de Salud: export del portal Porchat."""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from conciliador.parsing.normalizadores import normalize_doc


def _nombre_titular(fila) -> str:
    partes = [fila.get("NOMBRE TITULAR"), fila.get("APELLIDO1 TITULAR"), fila.get("APELLIDO2 TITULAR")]
    return " ".join(str(p).strip() for p in partes if pd.notna(p) and str(p).strip())


def _nombre_beneficiario(fila) -> str:
    partes = [fila.get("NOMBRE BENEFICIARIO"), fila.get("APELLIDO1 BENEFICIARIO"), fila.get("APELLIDO2 BENEFICIARIO")]
    return " ".join(str(p).strip() for p in partes if pd.notna(p) and str(p).strip())


def cargar_cobro_salud(ruta) -> pd.DataFrame:
    df = pd.read_excel(ruta)
    out = pd.DataFrame({
        "placa": "",
        "documento": df["DOCUMENTO"].apply(normalize_doc),
        "nombre": df.apply(_nombre_beneficiario, axis=1),
        "nombre_titular": df.apply(_nombre_titular, axis=1),
        "valor_cobro": pd.to_numeric(df["TARIFA"], errors="coerce").fillna(0.0),
        "valor_iva_cobro": pd.to_numeric(df["IVA"], errors="coerce").fillna(0.0),
    })
    out["valor_total_cobro"] = out["valor_cobro"] + out["valor_iva_cobro"]
    return out[out["documento"] != ""]


def periodo_desde_porchat(ruta, mes: int | None = None, anio: int | None = None) -> tuple[int, int]:
    """El periodo real de facturacion viene en la columna PERIODO del Porchat,
    en formato mm/dd/yyyy (ej. '07/01/2026' = Julio 2026) -- se prefiere este
    dato sobre el nombre del archivo porque en la practica puede venir mal
    etiquetado (Zoho/Porchat traen el mes de exportacion, no el de cobro)."""
    if mes and anio:
        return mes, anio
    df = pd.read_excel(ruta, usecols=["PERIODO"])
    valores = df["PERIODO"].dropna().astype(str)
    if valores.empty:
        return mes or datetime.now().month, anio or datetime.now().year
    periodo_str = valores.mode().iloc[0]
    fecha = pd.to_datetime(periodo_str, format="%m/%d/%Y", errors="coerce")
    if pd.isna(fecha):
        fecha = pd.to_datetime(periodo_str, dayfirst=True, errors="coerce")
    return mes or fecha.month, anio or fecha.year
