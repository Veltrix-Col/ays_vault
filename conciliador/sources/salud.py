"""Adapter del archivo de cobro de Salud: hay dos formatos posibles segun de
donde se descargue --

  - "Porchat": export historico del portal Porchat (columnas en mayusculas:
    DOCUMENTO, TARIFA, IVA, PERIODO, etc.), en .xlsx.
  - "Sura directo": cuando Porchat no esta disponible y se descarga
    directo del portal de Sura (columnas en frases: 'Numero de
    identificacion (asegurado)', 'Prima periodo', 'Fecha', etc.), con
    formato de moneda colombiano ('.' miles, ',' decimales) en vez del que
    usa Porchat. Suele llegar con extension .xls, pero el contenido real
    puede ser el binario legado (OLE2/BIFF) o un .xlsx (zip) segun la
    exportacion -- no se fuerza un `engine` de pandas al leerlo para que la
    deteccion por contenido (`pd.read_excel`/`ExcelFile`) elija el correcto
    en cualquiera de los dos casos.

`cargar_cobro_salud()`/`inferir_periodo_salud()` detectan el formato mirando
las columnas del archivo (una columna centinela por formato) y despachan al
loader correspondiente: quien sube el archivo no elige el formato, el
sistema lo reconoce solo."""

from __future__ import annotations

import os
from datetime import datetime

import pandas as pd

from conciliador.domain.exceptions import FormatoArchivoNoReconocidoError
from conciliador.parsing.normalizadores import normalize_doc, parse_cop_es

_COLUMNA_CENTINELA_PORCHAT = "DOCUMENTO"
_COLUMNA_CENTINELA_SURA = "Numero de identificacion (asegurado)"


def _texto(valor) -> str:
    return str(valor).strip() if pd.notna(valor) else ""


def _columnas(ruta) -> pd.Index:
    return pd.read_excel(ruta, sheet_name=0, nrows=0).columns


def _detectar_formato(ruta) -> str:
    columnas = _columnas(ruta)
    if _COLUMNA_CENTINELA_PORCHAT in columnas:
        return "porchat"
    if _COLUMNA_CENTINELA_SURA in columnas:
        return "sura"
    archivo = os.path.basename(str(ruta))
    raise FormatoArchivoNoReconocidoError(
        f"'{archivo}' no corresponde a ningun formato de cobro de Salud reconocido "
        f"(se esperaba la columna '{_COLUMNA_CENTINELA_PORCHAT}' del formato Porchat "
        f"o '{_COLUMNA_CENTINELA_SURA}' del formato Sura directo)."
    )


def _nombre_titular_porchat(fila) -> str:
    partes = [fila.get("NOMBRE TITULAR"), fila.get("APELLIDO1 TITULAR"), fila.get("APELLIDO2 TITULAR")]
    return " ".join(str(p).strip() for p in partes if pd.notna(p) and str(p).strip())


def _nombre_beneficiario_porchat(fila) -> str:
    partes = [fila.get("NOMBRE BENEFICIARIO"), fila.get("APELLIDO1 BENEFICIARIO"), fila.get("APELLIDO2 BENEFICIARIO")]
    return " ".join(str(p).strip() for p in partes if pd.notna(p) and str(p).strip())


def _cargar_cobro_salud_porchat(ruta) -> pd.DataFrame:
    df = pd.read_excel(ruta)
    out = pd.DataFrame({
        "placa": "",
        "documento": df["DOCUMENTO"].apply(normalize_doc),
        "nombre": df.apply(_nombre_beneficiario_porchat, axis=1),
        "nombre_titular": df.apply(_nombre_titular_porchat, axis=1),
        "valor_cobro": pd.to_numeric(df["TARIFA"], errors="coerce").fillna(0.0),
        "valor_iva_cobro": pd.to_numeric(df["IVA"], errors="coerce").fillna(0.0),
    })
    out["valor_total_cobro"] = out["valor_cobro"] + out["valor_iva_cobro"]
    return out[out["documento"] != ""]


def _cargar_cobro_salud_sura(ruta) -> pd.DataFrame:
    """'Prima total' del archivo no se usa: se recalcula como valor_cobro +
    valor_iva_cobro, igual que en el formato Porchat, en vez de confiar en el
    total que trae el archivo ('Descuento EPS SURA' es informativo y no
    participa en esa suma segun los archivos de muestra)."""
    df = pd.read_excel(ruta, sheet_name=0)
    out = pd.DataFrame({
        "placa": "",
        "documento": df[_COLUMNA_CENTINELA_SURA].apply(normalize_doc),
        "nombre": df["Nombre Asegurado"].apply(_texto),
        "nombre_titular": df["Nombre Afiliado"].apply(_texto),
        "valor_cobro": df["Prima periodo"].apply(parse_cop_es),
        "valor_iva_cobro": df["IVA"].apply(parse_cop_es),
    })
    out["valor_total_cobro"] = out["valor_cobro"] + out["valor_iva_cobro"]
    return out[out["documento"] != ""]


def cargar_cobro_salud(ruta) -> pd.DataFrame:
    formato = _detectar_formato(ruta)
    if formato == "porchat":
        return _cargar_cobro_salud_porchat(ruta)
    return _cargar_cobro_salud_sura(ruta)


def _periodo_desde_columna(ruta, *, columna: str, formato_fecha: str | None) -> tuple[int, int] | None:
    df = pd.read_excel(ruta, usecols=[columna])
    valores = df[columna].dropna().astype(str)
    if valores.empty:
        return None
    valor_frecuente = valores.mode().iloc[0]
    fecha = pd.to_datetime(valor_frecuente, format=formato_fecha, errors="coerce") if formato_fecha else pd.NaT
    if pd.isna(fecha):
        fecha = pd.to_datetime(valor_frecuente, dayfirst=True, errors="coerce")
    return None if pd.isna(fecha) else (fecha.month, fecha.year)


def inferir_periodo_salud(ruta, mes: int | None = None, anio: int | None = None) -> tuple[int, int]:
    """El periodo real de facturacion se lee del archivo -- columna PERIODO
    en formato Porchat (mm/dd/yyyy, ej. '07/01/2026' = Julio 2026), columna
    Fecha en formato Sura directo (ISO, ej. '2026-08-01' = Agosto 2026) --
    en vez de confiar en el nombre del archivo, que en la practica puede
    venir mal etiquetado (Zoho/Porchat traen el mes de exportacion, no el
    de cobro)."""
    if mes and anio:
        return mes, anio
    formato = _detectar_formato(ruta)
    if formato == "porchat":
        periodo = _periodo_desde_columna(ruta, columna="PERIODO", formato_fecha="%m/%d/%Y")
    else:
        # Formato ISO explicito como intento primario: con el fallback generico
        # (dayfirst=True) una fecha ISO sin ambiguedad como '2026-08-01' se
        # interpreta mal (dateutil la lee como 2026-01-08, dia 8 de enero).
        periodo = _periodo_desde_columna(ruta, columna="Fecha", formato_fecha="%Y-%m-%d")
    if periodo is None:
        return mes or datetime.now().month, anio or datetime.now().year
    return mes or periodo[0], anio or periodo[1]
