"""Equivalente por API de `conciliador.sources.zoho`: reemplaza Personas_Zoho y
la Relacion de asegurados (los dos archivos que Zoho exporta igual para todos
los ramos) por consultas directas contra Zoho CRM, via `ays_zoho_sdk`.

Personas -> modulo `Contacts` (label real en el CRM: "Personas").
Asegurados -> modulo `Riesgos1` (label real en el CRM: "Asegurados"), con los
lookups a `Contacts` (asegurado/afiliado), `Riesgos` (Key Riesgo/placa) y
`Polizas` (filtro).

COQL de Zoho solo admite referenciar hasta 2 modulos relacionados por
consulta (contando el WHERE): confirmado empiricamente contra Sandbox -- con
3 modulos (Contacts + Riesgos + Polizas) Zoho no devuelve error, descarta en
silencio los campos del modulo "de mas". Por eso la relacion se arma con dos
consultas (una con Riesgo, otra con Asegurado/Afiliado, ambas filtradas por
Polizas via WHERE) unidas por `id` en vez de un unico select con los 3.

Novedades no es un modulo separado (no existe un objeto de historial de
movimientos en el CRM), pero la clasificacion que hace el reporte manual de
novedades (Descongelado/Modificacion/Devolucion/Retiro/Ingreso/Existente) se
puede recalcular con campos nativos de `Riesgos1` que ya se consultan para la
relacion, mas dos que no: `Fecha_de_modificaci_n` y `Fecha_fin_congelaci_n`.
La logica de clasificacion (`_clasificar_novedad`) replica exactamente el
CASE de negocio ya validado en Zoho Analytics para esta misma poliza; no es
una reinterpretacion.

No importa Django ni `integrations.zoho`: recibe un `ZohoFacade` ya
autenticado (inyectado por quien orquesta, igual que `sources.zoho` recibe
una ruta de archivo ya materializada), para que `conciliador` siga siendo
usable fuera de Django.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator

import pandas as pd

from ays_zoho_sdk import ZohoFacade

from conciliador.parsing.normalizadores import normalize_doc, normalize_plate
from conciliador.sources.base import texto

_CAMPOS_BASE = (
    "id, Estado, Fecha_ingreso_riesgo, Fecha_salida_riesgo, "
    "Pago_EMPLEADO_Sin_IVA, Prima, Parentesco, Name, Riesgo.Name"
)
_CAMPOS_CONTACTOS = (
    "id, Asegurado.N_mero_de_ID, Asegurado.Full_Name, "
    "Contacto_facturaci_n_dividida_colectivas.N_mero_de_ID, "
    "Contacto_facturaci_n_dividida_colectivas.Full_Name"
)
_PAGINA_COQL = 200
# IDs por lote de la busqueda "in (...)" de personas: 300 documentos de ~12
# digitos entrecomillados caben holgadamente en MAX_COQL_LENGTH (10 000).
_LOTE_PERSONAS = 300


def _escapar_coql(valor: str) -> str:
    return valor.replace("\\", "\\\\").replace("'", "\\'")


def _lotes(valores: list[str], tamano: int) -> Iterator[list[str]]:
    for inicio in range(0, len(valores), tamano):
        yield valores[inicio:inicio + tamano]


def resolver_id_poliza(zoho: ZohoFacade, *, poliza: str) -> str | None:
    """Id de registro (no el numero de poliza/`Name`) del modulo `Polizas`,
    para armar enlaces directos a la interfaz web de Zoho CRM. None si la
    poliza no existe en el perfil consultado."""
    poliza_segura = _escapar_coql(poliza)
    pagina = zoho.coql.execute(f"select id from Polizas where Name = '{poliza_segura}'", limit=1)
    if not pagina.records:
        return None
    return str(pagina.records[0]["id"])


_CAMPOS_COBROS = ("id, Name, Ramo, Numero_de_cuota, "
                   "Certificado_Fecha_de_inicio_de_vigencia, Certificado_Fecha_de_Fin_de_vigencia")
_LIMITE_COBROS = 50


def resolver_cobros_poliza(zoho: ZohoFacade, *, poliza: str) -> list[dict[str, str | None]]:
    """Candidatos de 'Cobro' (modulo `Opeeraciones`, tab `CustomModule6` en la
    interfaz web de Zoho CRM) para una poliza.

    Puede haber varias Operaciones vigentes al mismo tiempo para una misma
    poliza (por ramo, por cuota): en vez de asumir una sola y arriesgar
    enlazar a la equivocada, se devuelve la lista completa -- mas reciente
    primero por vigencia -- para que quien concilia elija a cual ir con la
    fecha de vigencia a la vista."""
    poliza_segura = _escapar_coql(poliza)
    query = (
        f"select {_CAMPOS_COBROS} from Opeeraciones where P_liza.Name = '{poliza_segura}' "
        "order by Certificado_Fecha_de_inicio_de_vigencia desc"
    )
    pagina = zoho.coql.execute(query, limit=_LIMITE_COBROS)
    return [
        {
            "id": str(fila["id"]),
            "nombre": fila.get("Name") or "",
            "ramo": fila.get("Ramo") or "",
            "numero_cuota": fila.get("Numero_de_cuota") or "",
            "vigencia_inicio": fila.get("Certificado_Fecha_de_inicio_de_vigencia"),
            "vigencia_fin": fila.get("Certificado_Fecha_de_Fin_de_vigencia"),
        }
        for fila in pagina.records
    ]


def cargar_personas_api(zoho: ZohoFacade, *, documentos: Iterable[str]) -> set[str]:
    """De los documentos recibidos (tipicamente la union de la relacion de
    asegurados y el archivo de cobro), devuelve el subconjunto que ya existe
    como contacto/persona en Zoho (modulo Contacts).

    Busqueda dirigida en vez de traer el modulo completo: Contacts puede
    tener miles de registros y lo unico que necesita
    `IngresoNuevoSinPersonaRule` es la pertenencia de un puñado de documentos
    puntuales."""
    # "0" es el centinela de normalize_doc() para "sin digitos"; enviarlo a Zoho
    # como filtro real (en vez de descartarlo) le dispara una respuesta no-JSON.
    normalizados = sorted({normalize_doc(valor) for valor in documentos} - {"", "0"})
    if not normalizados:
        return set()

    encontrados: set[str] = set()
    for lote in _lotes(normalizados, _LOTE_PERSONAS):
        valores_in = ", ".join(f"'{_escapar_coql(doc)}'" for doc in lote)
        query = f"select N_mero_de_ID from Contacts where N_mero_de_ID in ({valores_in})"
        pagina = zoho.coql.execute(query, limit=len(lote))
        encontrados.update(normalize_doc(fila.get("N_mero_de_ID")) for fila in pagina.records)
    return encontrados - {""}


_COLUMNAS_RELACION = ["placa", "documento", "nombre", "documento_titular", "nombre_titular",
                       "parentesco", "subriesgo", "estado_asegurado", "fecha_ingreso",
                       "fecha_retiro", "pago_asegurado", "pago_empresa", "valor_zoho"]


def _consultar_riesgos1(zoho: ZohoFacade, *, campos: str, poliza_segura: str) -> pd.DataFrame:
    filas: list[dict[str, object]] = []
    offset = 0
    while True:
        query = f"select {campos} from Riesgos1 where P_liza.Name = '{poliza_segura}'"
        pagina = zoho.coql.execute(query, offset=offset, limit=_PAGINA_COQL)
        filas.extend(pagina.records)
        if not pagina.more_records or len(pagina.records) < _PAGINA_COQL:
            break
        offset += _PAGINA_COQL
    return pd.DataFrame(filas)


def cargar_relacion_api(zoho: ZohoFacade, *, poliza: str) -> pd.DataFrame:
    """Equivalente API de `cargar_relacion_zoho()`: consulta el modulo
    `Riesgos1` (Asegurados) filtrado por poliza.

    Dos consultas unidas por `id` (ver limite de 2 modulos relacionados por
    consulta explicado arriba), no una: la primera trae los campos propios y
    el lookup a Riesgo, la segunda los lookups a Asegurado/Afiliado."""
    poliza_segura = _escapar_coql(poliza)
    base = _consultar_riesgos1(zoho, campos=_CAMPOS_BASE, poliza_segura=poliza_segura)
    if base.empty:
        return pd.DataFrame(columns=_COLUMNAS_RELACION)
    contactos = _consultar_riesgos1(zoho, campos=_CAMPOS_CONTACTOS, poliza_segura=poliza_segura)
    df = base.merge(contactos, on="id", how="left") if not contactos.empty else base

    pago_asegurado = pd.to_numeric(df.get("Pago_EMPLEADO_Sin_IVA"), errors="coerce").fillna(0.0)
    pago_empresa = pd.to_numeric(df.get("Prima"), errors="coerce").fillna(0.0)
    return pd.DataFrame({
        "placa": df.get("Riesgo.Name", "").apply(normalize_plate),
        "documento": df.get("Asegurado.N_mero_de_ID", "").apply(normalize_doc),
        "nombre": df.get("Asegurado.Full_Name", "").apply(texto),
        "documento_titular": df.get("Contacto_facturaci_n_dividida_colectivas.N_mero_de_ID", "").apply(normalize_doc),
        "nombre_titular": df.get("Contacto_facturaci_n_dividida_colectivas.Full_Name", "").apply(texto),
        "parentesco": df.get("Parentesco", "").apply(texto),
        "subriesgo": df.get("Name", "").apply(texto),
        "estado_asegurado": df.get("Estado", "").astype(str).str.strip(),
        "fecha_ingreso": pd.to_datetime(df.get("Fecha_ingreso_riesgo"), errors="coerce"),
        "fecha_retiro": pd.to_datetime(df.get("Fecha_salida_riesgo"), errors="coerce"),
        "pago_asegurado": pago_asegurado,
        "pago_empresa": pago_empresa,
        "valor_zoho": pago_asegurado + pago_empresa,
    })


_CAMPOS_NOVEDADES_BASE = (
    "id, Fecha_ingreso_riesgo, Fecha_salida_riesgo, Fecha_de_modificaci_n, "
    "Fecha_fin_congelaci_n, Pago_total, Pago_EMPLEADO_Sin_IVA, Observaciones, Riesgo.Name"
)
_COLUMNAS_NOVEDADES = ["placa", "documento", "nombre", "estado_novedad", "fecha_novedad",
                        "fecha_ingreso", "fecha_retiro", "valor_novedad", "observaciones"]
# Ventana de "Ingreso reciente" del CASE de Analytics: 45 dias atras a 15 adelante.
_VENTANA_INGRESO_ANTES = pd.Timedelta(days=45)
_VENTANA_INGRESO_DESPUES = pd.Timedelta(days=15)


def _clasificar_novedad(fila: pd.Series, *, ahora: pd.Timestamp) -> tuple[str, object]:
    """Replica, fila a fila, las dos expresiones CASE del query de Analytics
    (Estado_Asegurado y Fecha_Novedad): son dos cadenas de prioridad
    independientes, no una sola -- por ejemplo Fecha_Novedad no exige que la
    fecha de ingreso caiga en la ventana de 45/15 dias que si exige el estado
    'Ingreso', y no tiene rama propia para 'Devolucion'."""
    congelacion = fila["_fecha_fin_congelacion"]
    modificacion = fila["_fecha_modificacion"]
    retiro = fila["fecha_retiro"]
    ingreso = fila["fecha_ingreso"]
    pago_con_iva = fila["_pago_total_con_iva"]

    if pd.notna(congelacion):
        estado = "Descongelado"
    elif pd.notna(modificacion):
        estado = "Modificación"
    elif pd.notna(pago_con_iva) and pago_con_iva < 0:
        estado = "Devolución"
    elif pd.notna(retiro):
        estado = "Retiro"
    elif pd.notna(ingreso) and (ahora - _VENTANA_INGRESO_ANTES) <= ingreso <= (ahora + _VENTANA_INGRESO_DESPUES):
        estado = "Ingreso"
    else:
        estado = "Existente"

    if pd.notna(congelacion):
        fecha = congelacion
    elif pd.notna(modificacion):
        fecha = modificacion
    elif pd.notna(retiro):
        fecha = retiro
    elif pd.notna(ingreso):
        fecha = ingreso
    else:
        fecha = pd.NaT
    return estado, fecha


def cargar_novedades_api(zoho: ZohoFacade, *, poliza: str) -> pd.DataFrame:
    """Equivalente API del reporte manual de novedades: mismos campos nativos
    de `Riesgos1` que `cargar_relacion_api()` mas los dos que solo necesita la
    clasificacion (`Fecha_de_modificaci_n`, `Fecha_fin_congelaci_n`), y la
    misma logica de negocio (`_clasificar_novedad`) que ya corre en el
    workspace de Zoho Analytics para esta poliza, pero calculada en Python en
    vez de en el motor SQL de Analytics."""
    poliza_segura = _escapar_coql(poliza)
    base = _consultar_riesgos1(zoho, campos=_CAMPOS_NOVEDADES_BASE, poliza_segura=poliza_segura)
    if base.empty:
        return pd.DataFrame(columns=_COLUMNAS_NOVEDADES)
    contactos = _consultar_riesgos1(zoho, campos=_CAMPOS_CONTACTOS, poliza_segura=poliza_segura)
    df = base.merge(contactos, on="id", how="left") if not contactos.empty else base

    intermedio = pd.DataFrame({
        "placa": df.get("Riesgo.Name", "").apply(normalize_plate),
        "documento": df.get("Asegurado.N_mero_de_ID", "").apply(normalize_doc),
        "nombre": df.get("Asegurado.Full_Name", "").apply(texto),
        "fecha_ingreso": pd.to_datetime(df.get("Fecha_ingreso_riesgo"), errors="coerce"),
        "fecha_retiro": pd.to_datetime(df.get("Fecha_salida_riesgo"), errors="coerce"),
        "valor_novedad": pd.to_numeric(df.get("Pago_EMPLEADO_Sin_IVA"), errors="coerce").fillna(0.0),
        "observaciones": df.get("Observaciones", "").apply(texto),
        "_fecha_modificacion": pd.to_datetime(df.get("Fecha_de_modificaci_n"), errors="coerce"),
        "_fecha_fin_congelacion": pd.to_datetime(df.get("Fecha_fin_congelaci_n"), errors="coerce"),
        "_pago_total_con_iva": pd.to_numeric(df.get("Pago_total"), errors="coerce"),
    })

    ahora = pd.Timestamp.now()
    clasificacion = intermedio.apply(lambda fila: _clasificar_novedad(fila, ahora=ahora), axis=1)
    intermedio["estado_novedad"] = clasificacion.apply(lambda par: par[0])
    intermedio["fecha_novedad"] = clasificacion.apply(lambda par: par[1])
    return intermedio[_COLUMNAS_NOVEDADES]
