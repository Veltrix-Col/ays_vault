"""Adapters compartidos por los sub-ramos de Vida Grupo (Voluntario, Deudores,
Patronal): todos reciben el mismo tipo de archivo de cobro de la aseguradora
(AVA), con las hojas 'Resumen', 'Cobro por tipo de operacion' y 'Desglose de
Coberturas'.

La llave de cruce contra la relacion de Zoho es compuesta:
ID_Afiliado + ID_Asegurado + Subriesgo (Subriesgo en Zoho == # RIESGO en el
desglose de coberturas de AVA).
"""

from __future__ import annotations

import pandas as pd

from ays_zoho_sdk import ZohoFacade

from conciliador.parsing.normalizadores import normalize_doc
from conciliador.sources.zoho import cargar_relacion_zoho
from conciliador.sources.zoho_api import cargar_relacion_api

COBERTURA_COLUMNAS = [
    "PRIMA VID", "PRIMA INV", "PRIMA IAM", "PRIMA EFG O EGI", "PRIMA BON", "PRIMA AP",
    "PRIMA REN", "PRIMA IPP", "PRIMA XMA", "PRIMA ITT", "PRIMA XRE", "PRIMA PERDIDA DE INGRESO",
]


def _riesgo_str(valor) -> str:
    if pd.isna(valor):
        return ""
    return str(int(valor))


def cargar_desglose_coberturas(ruta) -> pd.DataFrame:
    """Carga la hoja 'Desglose de Coberturas' del Excel de AVA. Detecta la
    fila de encabezado automaticamente (el archivo trae varias filas de
    texto introductorio antes de la tabla) y descarta la fila de totales.

    El archivo suele llegar con extension .xls aunque internamente es un
    .xlsx (zip): se fuerza engine='openpyxl' en vez de dejar que pandas
    elija xlrd solo por la extension del nombre."""
    crudo = pd.read_excel(ruta, sheet_name="Desglose de Coberturas", engine="openpyxl", header=None)
    fila_header = crudo.index[crudo.iloc[:, 0].astype(str).str.strip() == "# RIESGO"][0]
    df = pd.read_excel(ruta, sheet_name="Desglose de Coberturas", engine="openpyxl", header=fila_header)
    return df.dropna(subset=["ID ASEGURADO"]).copy()


def validar_suma_coberturas(df: pd.DataFrame) -> pd.DataFrame:
    """Verifica que, fila a fila, la suma de las primas por cobertura + IVA
    sea igual a PRIMA ASEGURADO. Devuelve solo las filas con diferencia >= 1."""
    cols_presentes = [c for c in COBERTURA_COLUMNAS if c in df.columns]
    suma = df[cols_presentes].sum(axis=1) + df.get("VALOR IVA", 0).fillna(0)
    diff = (suma - df["PRIMA ASEGURADO"]).round(2)
    anomalas = df[diff.abs() >= 1].copy()
    anomalas["_suma_coberturas"] = suma[diff.abs() >= 1]
    anomalas["_diferencia"] = diff[diff.abs() >= 1]
    return anomalas


def cargar_cobro_vg(ruta) -> pd.DataFrame:
    df = cargar_desglose_coberturas(ruta)
    id_afiliado = df["ID AFILIADO"].apply(normalize_doc)
    id_asegurado = df["ID ASEGURADO"].apply(normalize_doc)
    riesgo = df["# RIESGO"].apply(_riesgo_str)
    valor_total = pd.to_numeric(df["PRIMA ASEGURADO"], errors="coerce").fillna(0.0)
    valor_iva = pd.to_numeric(df.get("VALOR IVA", 0), errors="coerce").fillna(0.0)
    out = pd.DataFrame({
        "placa": "",
        "documento": id_asegurado,
        "documento_titular": id_afiliado,
        "nombre": df["NOMBRE DEL ASEGURADO"].astype(str).str.strip(),
        "nombre_titular": "",
        "subriesgo": riesgo,
        "clave": id_afiliado + "_" + id_asegurado + "_" + riesgo,
        "valor_cobro": valor_total - valor_iva,
        "valor_iva_cobro": valor_iva,
        "valor_total_cobro": valor_total,
    })
    return out[(out["documento"] != "") & (out["subriesgo"] != "")]


def _con_clave_compuesta(relacion: pd.DataFrame) -> pd.DataFrame:
    relacion["clave"] = (relacion["documento_titular"] + "_" + relacion["documento"]
                          + "_" + relacion["subriesgo"].astype(str))
    return relacion


def cargar_relacion_vg(ruta) -> pd.DataFrame:
    """Relacion de Zoho + llave compuesta ID_Afiliado_ID_Asegurado_Subriesgo,
    lista para cruzar contra cargar_cobro_vg()."""
    return _con_clave_compuesta(cargar_relacion_zoho(ruta))


def cargar_relacion_vg_api(zoho: ZohoFacade, *, poliza: str) -> pd.DataFrame:
    """Equivalente API de `cargar_relacion_vg()`."""
    return _con_clave_compuesta(cargar_relacion_api(zoho, poliza=poliza))


def datos_extra_vg(ruta) -> dict[str, pd.DataFrame]:
    """Datos auxiliares del ramo que no viven en la comparacion Zoho-vs-cobro
    generica pero que si necesitan reglas propias de VG (SumaCoberturasRule)."""
    return {"desglose_coberturas": cargar_desglose_coberturas(ruta)}


def cargar_novedades_cliente_deudor(ruta, sheet_name: str = "VIDA DEUDORES") -> pd.DataFrame:
    """VG Deudores no tiene reporte de novedades de Zoho: las altas/bajas las
    genera el banco/financiera dueño de la cartera. Se adapta su reporte de
    cartera (cedula, obligacion, monto, saldo) al esquema comun de novedades,
    para poder usarlo como contexto de respaldo de cada incidente."""
    df = pd.read_excel(ruta, sheet_name=sheet_name)
    df.columns = df.columns.str.strip()
    documento = df["CEDULA"].apply(normalize_doc)
    out = pd.DataFrame({
        "placa": "",
        "documento": documento,
        "nombre": df["NOMBRE"].astype(str).str.strip(),
        "estado_novedad": "Obligacion vigente en cartera del cliente",
        "fecha_novedad": pd.to_datetime(df["FECHA PRESTAMO"], errors="coerce"),
        "fecha_ingreso": pd.to_datetime(df["FECHA PRESTAMO"], errors="coerce"),
        "fecha_retiro": pd.NaT,
        "valor_novedad": pd.to_numeric(df["MONTO"], errors="coerce"),
        "observaciones": ("Obligacion No. " + df["No. OBLIGACION"].astype(str)
                           + " - Saldo actual: " + df["SALDO TOTAL"].astype(str)),
    })
    return out[out["documento"] != ""]
