"""Adapters para los 3 archivos que Zoho exporta igual para todos los ramos:
Personas_Zoho, la Relacion de asegurados y el Reporte de novedades.

Si el ramo no usa placa (ej. Salud, VG) 'Key_Riesgo' viene vacio en el
Excel y la columna 'placa' del DataFrame normalizado queda en blanco; el
cruce se hace entonces por 'documento' (o por una llave compuesta que arma
el `sources` especifico del ramo, ver `conciliador.sources.vg`).
"""

from __future__ import annotations

import os

import pandas as pd

from conciliador.parsing.normalizadores import normalize_doc, normalize_plate, parse_cop
from conciliador.sources.base import columna, texto

NOVEDADES_COLUMNAS = ["placa", "documento", "nombre", "estado_novedad", "fecha_novedad",
                       "fecha_ingreso", "fecha_retiro", "valor_novedad", "observaciones"]


def cargar_personas_zoho(ruta) -> set[str]:
    """Documentos ya registrados como contacto/persona en Zoho."""
    df = pd.read_excel(ruta)
    columnas_id = [c for c in df.columns if "ID" in c.upper()]
    if not columnas_id:
        raise ValueError(f"'{ruta}' no tiene ninguna columna de ID reconocible")
    return set(df[columnas_id[0]].apply(normalize_doc)) - {""}


def cargar_relacion_zoho(ruta, sheet_name=0) -> pd.DataFrame:
    """Carga la 'Relacion de asegurados' de Zoho (misma estructura de 35
    columnas en todos los ramos observados)."""
    archivo = os.path.basename(str(ruta))
    df = pd.read_excel(ruta, sheet_name=sheet_name)
    pago_asegurado = columna(df, "Pago ASEGURADO (Sin IVA)", archivo=archivo).apply(parse_cop)
    pago_empresa = columna(df, "Pago EMPRESA (Sin IVA)", archivo=archivo).apply(parse_cop)
    return pd.DataFrame({
        "placa": columna(df, "Key_Riesgo", archivo=archivo).apply(normalize_plate),
        "documento": columna(df, "Id_Asegurado", archivo=archivo).apply(normalize_doc),
        "nombre": columna(df, "Nombre_Asegurado", archivo=archivo).apply(texto),
        "documento_titular": columna(df, "Id_Afiliado", archivo=archivo).apply(normalize_doc),
        "nombre_titular": columna(df, "Nombre_Afiliado", archivo=archivo).apply(texto),
        "parentesco": df["Parentesco"].apply(texto) if "Parentesco" in df.columns else "",
        "subriesgo": df["Subriesgo"].apply(texto) if "Subriesgo" in df.columns else "",
        "estado_asegurado": columna(df, "Estado asegurado", archivo=archivo).astype(str).str.strip(),
        "fecha_ingreso": pd.to_datetime(columna(df, "Fecha ingreso", archivo=archivo), dayfirst=True, errors="coerce"),
        "fecha_retiro": pd.to_datetime(columna(df, "Fecha retiro", archivo=archivo), dayfirst=True, errors="coerce"),
        "pago_asegurado": pago_asegurado,
        "pago_empresa": pago_empresa,
        # Suma Pago ASEGURADO + Pago EMPRESA: en Movilidad/Salud, Pago EMPRESA
        # es siempre 0, asi que esto no cambia esos ramos; en VG Voluntario y
        # Deudores la compañia factura la suma de ambos.
        "valor_zoho": pago_asegurado + pago_empresa,
    })


def cargar_novedades_vacio() -> pd.DataFrame:
    """Placeholder para cuando aun no se cuenta con el reporte de novedades del ramo."""
    return pd.DataFrame(columns=NOVEDADES_COLUMNAS)


def cargar_novedades_zoho(ruta, sheet_name=0) -> pd.DataFrame:
    archivo = os.path.basename(str(ruta))
    df = pd.read_excel(ruta, sheet_name=sheet_name)
    return pd.DataFrame({
        "placa": columna(df, "Key_Riesgo", archivo=archivo).apply(normalize_plate),
        "documento": columna(df, "Id_Asegurado", archivo=archivo).apply(normalize_doc),
        "nombre": columna(df, "Nombre_Asegurado", archivo=archivo).apply(texto),
        "estado_novedad": columna(df, "Estado", archivo=archivo).astype(str).str.strip(),
        "fecha_novedad": pd.to_datetime(columna(df, "Fecha_Novedad", archivo=archivo), errors="coerce"),
        "fecha_ingreso": pd.to_datetime(columna(df, "Fecha ingreso", archivo=archivo), errors="coerce"),
        "fecha_retiro": pd.to_datetime(columna(df, "Fecha retiro", archivo=archivo), errors="coerce"),
        "valor_novedad": columna(df, "Pago ASEGURADO (Sin IVA)", archivo=archivo).apply(parse_cop),
        "observaciones": df["Observaciones"].apply(texto) if "Observaciones" in df.columns else "",
    })
