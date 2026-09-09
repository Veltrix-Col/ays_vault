"""Adapters compartidos por los sub-ramos de Vida Grupo (Voluntario, Deudores,
Patronal). VG Voluntario recibe siempre el archivo de cobro de la aseguradora
(AVA), con las hojas 'Resumen', 'Cobro por tipo de operacion' y 'Desglose de
Coberturas'. VG Deudores puede recibir ese mismo AVA, o -- cuando no esta
disponible -- un export alternativo de "Riesgos vigentes" ligado a la cartera
de creditos (columnas 'ID Afiliado', 'ID Asegurado', 'Certificado', 'Prima
Periodo', etc., a veces con un bloque de metadata -- Poliza, Fecha de
exportacion -- antes de la tabla real). `cargar_cobro_vg_deudores()` detecta
cual de los dos es mirando si el archivo tiene la hoja 'Desglose de
Coberturas'.

La llave de cruce contra la relacion de Zoho es compuesta:
ID_Afiliado + ID_Asegurado + Subriesgo. En AVA, Subriesgo == # RIESGO del
desglose de coberturas; en el export de Riesgos vigentes, Subriesgo ==
Certificado (una misma persona puede tener varios creditos vigentes a la vez,
cada uno con su propio Certificado, igual que pasaria con varios subriesgos).
"""

from __future__ import annotations

import os
import re

import pandas as pd

from ays_zoho_sdk import ZohoFacade

from conciliador.domain.exceptions import FormatoArchivoNoReconocidoError
from conciliador.parsing.normalizadores import normalize_doc, parse_cop
from conciliador.sources.zoho import cargar_relacion_zoho
from conciliador.sources.zoho_api import cargar_relacion_api

_HOJA_AVA = "Desglose de Coberturas"
_COLUMNA_CENTINELA_RIESGOS_VIGENTES = "ID Afiliado"
_MAX_FILAS_ENCABEZADO = 15
# 'Código de Crédito' -> en Zoho, Riesgos1.Número crédito (ver
# conciliacion.services.processor.actualizar_numero_credito). Solo viene en
# algunas exportaciones del archivo de Riesgos vigentes, y su nombre de
# columna llega con la codificacion corrompida en el archivo real ('C�digo de
# Cr�dito'): el patron usa '.' donde va cada tilde para tolerar tanto el
# nombre correcto como el corrompido.
_PATRON_COLUMNA_CODIGO_CREDITO = re.compile(r"^c.digo de cr.dito$", re.IGNORECASE)

COBERTURA_COLUMNAS = [
    "PRIMA VID", "PRIMA INV", "PRIMA IAM", "PRIMA EFG O EGI", "PRIMA BON", "PRIMA AP",
    "PRIMA REN", "PRIMA IPP", "PRIMA XMA", "PRIMA ITT", "PRIMA XRE", "PRIMA PERDIDA DE INGRESO",
]


def _riesgo_str(valor) -> str:
    if pd.isna(valor):
        return ""
    return str(int(valor))


def _texto_numerico(valor) -> str:
    """Como `_riesgo_str`, pero tolera texto ademas de numeros: usado en
    'Código de Crédito', que llega como int64 cuando ninguna fila tiene el
    dato vacio, pero pandas sube la columna completa a float64 (agregando
    '.0' al convertir a texto ingenuamente) en cuanto una sola fila no trae
    valor (columna con NaN)."""
    if pd.isna(valor):
        return ""
    if isinstance(valor, float) and valor.is_integer():
        return str(int(valor))
    return str(valor).strip()


def cargar_desglose_coberturas(ruta) -> pd.DataFrame:
    """Carga la hoja 'Desglose de Coberturas' del Excel de AVA. Detecta la
    fila de encabezado automaticamente (el archivo trae varias filas de
    texto introductorio antes de la tabla) y descarta la fila de totales.

    El archivo suele llegar con extension .xls aunque internamente es un
    .xlsx (zip): se fuerza engine='openpyxl' en vez de dejar que pandas
    elija xlrd solo por la extension del nombre."""
    crudo = pd.read_excel(ruta, sheet_name=_HOJA_AVA, engine="openpyxl", header=None)
    fila_header = crudo.index[crudo.iloc[:, 0].astype(str).str.strip() == "# RIESGO"][0]
    df = pd.read_excel(ruta, sheet_name=_HOJA_AVA, engine="openpyxl", header=fila_header)
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


def _es_formato_ava(ruta) -> bool:
    with pd.ExcelFile(ruta) as xls:
        return _HOJA_AVA in xls.sheet_names


def _fila_encabezado_riesgos_vigentes(ruta) -> int:
    """El export de 'Riesgos vigentes' a veces trae un bloque de metadata
    (Poliza, Fecha de exportacion) antes de la tabla real -- se busca la fila
    que tiene 'ID Afiliado' como encabezado en vez de asumir siempre la fila 0
    (mismo criterio que `cargar_desglose_coberturas` usa para AVA). La
    columna se elige como ancla porque, a diferencia de otras columnas del
    archivo (p. ej. 'Código de Crédito'), nunca lleva tildes y no se ve
    afectado por la codificacion inconsistente que trae ese export."""
    crudo = pd.read_excel(ruta, sheet_name=0, header=None, nrows=_MAX_FILAS_ENCABEZADO)
    coincidencias = crudo.index[(crudo == _COLUMNA_CENTINELA_RIESGOS_VIGENTES).any(axis=1)]
    if coincidencias.empty:
        raise FormatoArchivoNoReconocidoError(
            f"'{os.path.basename(str(ruta))}' no corresponde a ningun formato de cobro de VG Deudores "
            f"reconocido (no tiene la hoja '{_HOJA_AVA}' de AVA ni la columna "
            f"'{_COLUMNA_CENTINELA_RIESGOS_VIGENTES}' del export de Riesgos vigentes)."
        )
    return coincidencias[0]


def _columna_codigo_credito(columnas) -> str | None:
    for columna in columnas:
        if _PATRON_COLUMNA_CODIGO_CREDITO.match(str(columna).strip()):
            return columna
    return None


def _cargar_cobro_vg_riesgos_vigentes(ruta) -> pd.DataFrame:
    """Export alternativo de 'Riesgos vigentes' ligado a la cartera de
    creditos (VG Deudores), cuando el AVA de la aseguradora no esta
    disponible. No trae Subriesgo/# RIESGO ni IVA: 'Certificado' hace las
    veces de Subriesgo (un mismo afiliado puede tener varios creditos
    vigentes a la vez, cada uno con su Certificado) y la prima se asume sin
    IVA (Vida Grupo tipicamente no lo cobra), igual que ya asume
    `cargar_cobro_vg` cuando el AVA no trae 'VALOR IVA'.

    'Prima Periodo' no siempre es numerico: en al menos una variante de este
    export llega como texto con simbolo de moneda y relleno de espacios (ej.
    '$                   430.00'), por eso se parsea con `parse_cop` en vez
    de `pd.to_numeric` (que descartaria esos valores a 0 en silencio).

    'codigo_credito' viene vacio ("") cuando la columna no esta en el archivo
    o la fila no trae valor -- `conciliador.service.ConciliacionService` solo
    arma la lista de pendientes para Zoho con las filas que si traen valor."""
    fila_header = _fila_encabezado_riesgos_vigentes(ruta)
    df = pd.read_excel(ruta, sheet_name=0, header=fila_header)
    id_afiliado = df["ID Afiliado"].apply(normalize_doc)
    id_asegurado = df["ID Asegurado"].apply(normalize_doc)
    riesgo = df["Certificado"].apply(_riesgo_str)
    valor_total = df["Prima Periodo"].apply(parse_cop)
    columna_credito = _columna_codigo_credito(df.columns)
    codigo_credito = df[columna_credito].apply(_texto_numerico) if columna_credito is not None else ""
    out = pd.DataFrame({
        "placa": "",
        "documento": id_asegurado,
        "documento_titular": id_afiliado,
        "nombre": df["Nombre"].astype(str).str.strip(),
        "nombre_titular": "",
        "subriesgo": riesgo,
        "clave": id_afiliado + "_" + id_asegurado + "_" + riesgo,
        "valor_cobro": valor_total,
        "valor_iva_cobro": 0.0,
        "valor_total_cobro": valor_total,
        "codigo_credito": codigo_credito,
    })
    return out[(out["documento"] != "") & (out["subriesgo"] != "")]


def cargar_cobro_vg_deudores(ruta) -> pd.DataFrame:
    """Punto de entrada del cobro para VG Deudores: detecta si el archivo es
    el AVA de siempre (hoja 'Desglose de Coberturas') o el export alternativo
    de Riesgos vigentes, y despacha al loader correspondiente. VG Voluntario
    sigue usando `cargar_cobro_vg` sin cambios -- el export alternativo es
    especifico de la cartera de creditos de Deudores."""
    if _es_formato_ava(ruta):
        return cargar_cobro_vg(ruta)
    return _cargar_cobro_vg_riesgos_vigentes(ruta)


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
    generica pero que si necesitan reglas propias de VG (SumaCoberturasRule).

    El export alternativo de Riesgos vigentes (VG Deudores) no tiene una hoja
    de desglose de coberturas equivalente a la de AVA -- sus columnas de
    prima por cobertura usan una taxonomia distinta (ITP/IMA/MAC MAH/IVA IAH
    en vez de INV/IAM/XMA/XRE) que no se puede mapear 1 a 1 sin confirmarla
    con el equipo, asi que por ahora se omite: `SumaCoberturasRule` ya sabe
    no reportar nada cuando 'desglose_coberturas' no esta en el dict."""
    if not _es_formato_ava(ruta):
        return {}
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
