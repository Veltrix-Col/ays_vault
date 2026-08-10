"""Adapter de Azure Content Understanding: convierte un recibo/factura en PDF
de la aseguradora en un DTO plano (`ReciboExtraido`) que las reglas consumen
sin saber nada de Azure.

Es el UNICO modulo que habla con Azure. El SDK (`azure-ai-contentunderstanding`,
`azure-identity`) se importa de forma perezosa dentro de `analizar_recibo`, para
que instalar el paquete base y correr los tests no requiera el extra `cu` ni
credenciales -- las reglas se prueban con un `ReciboExtraido` fabricado, igual
que `SumaCoberturasRule` se prueba con un DataFrame sintetico.

Configuracion por servicio (Salud / Vida / Movilidad): cada analyzer tiene su
propio endpoint, analyzer_id y KEY. La key SIEMPRE se lee de variable de entorno
(nunca vive en el repo); endpoint/analyzer/api_version tienen un default no
secreto sobreescribible por env. Variables por servicio:

    AZURE_CU_<SERVICIO>_KEY          (obligatoria para llamar; su ausencia degrada a None)
    AZURE_CU_<SERVICIO>_ENDPOINT     (opcional; si no, usa el default)
    AZURE_CU_<SERVICIO>_ANALYZER_ID  (opcional; si no, usa el default)
    AZURE_CU_<SERVICIO>_API_VERSION  (opcional; si no, usa el default)

donde <SERVICIO> es SALUD, VIDA o MOVILIDAD.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# Endpoint / analyzer / api_version por defecto (NO secretos). Solo se conoce el
# recurso de desarrollo comun de sample.py; en produccion cada servicio deberia
# sobreescribir endpoint y analyzer_id via env.
_ENDPOINT_DEV = "https://ays-cu-renovaciones-dev.services.ai.azure.com/"
_ANALYZER_DEV = "ays_extractor_reporte_dev"
_API_VERSION_DEV = "2025-11-01"

# Claves candidatas para el valor numerico de cada item de `listado_riesgos`.
# El esquema exacto puede variar; se busca por estas y, si ninguna aparece, se
# cae al primer valor numerico del item.
_CLAVES_VALOR_RIESGO = ("valor_total", "valor", "valor_riesgo", "prima", "monto", "value")


# ---------------------------------------------------------------------------
# Extraccion de campos del resultado crudo de Content Understanding
# (portado de sample.py; resuelve recursivamente arrays y objetos).
# ---------------------------------------------------------------------------
def extract_field_value(field):
    """Devuelve el valor real de un campo extraido por Content Understanding.

    Cada campo trae un "type" y una clave de valor asociada (valueString,
    valueNumber, valueArray, valueObject, ...). Resuelve recursivamente arrays
    y objetos para devolver datos de Python planos.
    """
    if not isinstance(field, dict):
        return field

    field_type = field.get("type")

    if field_type == "array":
        return [extract_field_value(item) for item in field.get("valueArray", [])]

    if field_type == "object":
        return {
            name: extract_field_value(sub_field)
            for name, sub_field in field.get("valueObject", {}).items()
        }

    value_key = f"value{field_type[:1].upper()}{field_type[1:]}" if field_type else None
    if value_key and value_key in field:
        return field[value_key]

    for key, value in field.items():
        if key.startswith("value"):
            return value

    return field.get("content")


def extract_all_fields(result_dict: dict) -> dict:
    """Recorre todos los 'contents' y devuelve {nombre_campo: valor}."""
    extracted: dict = {}
    for content in result_dict.get("contents", []):
        for name, field in content.get("fields", {}).items():
            extracted[name] = extract_field_value(field)
    return extracted


# ---------------------------------------------------------------------------
# DTO plano del recibo (lo que consume la regla)
# ---------------------------------------------------------------------------
def _a_float(valor) -> float:
    """Convierte a float tolerando strings con separadores ('1.234.567,89',
    '$ 1,234.56', '167.171'). Ante lo irrecuperable devuelve 0.0."""
    if valor is None:
        return 0.0
    if isinstance(valor, (int, float)):
        return float(valor)
    texto = str(valor).strip()
    if not texto:
        return 0.0
    texto = texto.replace("$", "").replace(" ", "")
    # Si tiene ambos separadores, el ultimo es el decimal.
    if "," in texto and "." in texto:
        if texto.rfind(",") > texto.rfind("."):
            texto = texto.replace(".", "").replace(",", ".")
        else:
            texto = texto.replace(",", "")
    elif "," in texto:
        texto = texto.replace(",", ".")
    try:
        return float(texto)
    except ValueError:
        return 0.0


def _valor_de_riesgo(item) -> float:
    """Extrae el valor numerico de un item de `listado_riesgos`, tolerando el
    nombre de la clave (ver `_CLAVES_VALOR_RIESGO`)."""
    if isinstance(item, dict):
        for clave in _CLAVES_VALOR_RIESGO:
            if clave in item and item[clave] is not None:
                return _a_float(item[clave])
        # Fallback: primer valor numerico (o numerico-como-texto) del item.
        for valor in item.values():
            convertido = _a_float(valor)
            if convertido:
                return convertido
        return 0.0
    return _a_float(item)


@dataclass(frozen=True)
class ReciboExtraido:
    """Esquema comun que devuelven los 3 analyzers (Salud / Vida / Movilidad)."""

    poliza: str
    valor_total: float
    riesgos: list

    @property
    def suma_riesgos(self) -> float:
        return round(sum(_valor_de_riesgo(r) for r in self.riesgos), 2)

    @classmethod
    def desde_campos(cls, campos: dict) -> "ReciboExtraido":
        """Construye el DTO desde el dict {nombre_campo: valor} de `extract_all_fields`."""
        riesgos = campos.get("listado_riesgos") or []
        if not isinstance(riesgos, list):
            riesgos = [riesgos]
        return cls(
            poliza=str(campos.get("poliza") or "").strip(),
            valor_total=_a_float(campos.get("valor_total")),
            riesgos=riesgos,
        )


# ---------------------------------------------------------------------------
# Configuracion por servicio
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class AnalyzerCU:
    servicio: str
    endpoint: str
    analyzer_id: str
    api_version: str
    key: str | None  # None => sin credencial en env; `analizar_recibo` degrada a None


_SERVICIOS_CU = ("salud", "vida", "movilidad")


def config_analizador(servicio: str) -> AnalyzerCU:
    """Resuelve la configuracion del analyzer de un servicio desde variables de
    entorno, con defaults no secretos para endpoint/analyzer/api_version."""
    if servicio not in _SERVICIOS_CU:
        raise ValueError(f"Servicio de Content Understanding no reconocido: '{servicio}'. "
                         f"Validos: {', '.join(_SERVICIOS_CU)}")
    prefijo = f"AZURE_CU_{servicio.upper()}"
    return AnalyzerCU(
        servicio=servicio,
        endpoint=os.environ.get(f"{prefijo}_ENDPOINT", _ENDPOINT_DEV),
        analyzer_id=os.environ.get(f"{prefijo}_ANALYZER_ID", _ANALYZER_DEV),
        api_version=os.environ.get(f"{prefijo}_API_VERSION", _API_VERSION_DEV),
        key=os.environ.get(f"{prefijo}_KEY"),
    )


# ---------------------------------------------------------------------------
# Llamada a Azure (import perezoso del SDK)
# ---------------------------------------------------------------------------
def analizar_recibo(pdf_path, servicio: str) -> ReciboExtraido | None:
    """Envia el PDF al analyzer del `servicio` y devuelve el recibo extraido.

    Degrada a `None` (sin romper la conciliacion) si: no hay ruta de PDF, el
    archivo no existe, no hay KEY en env para ese servicio, o el SDK de Azure
    no esta instalado / la llamada falla. La regla que consume esto traduce el
    `None` a un incidente 'N/D' visible en el reporte.
    """
    if not pdf_path or not os.path.exists(pdf_path):
        return None

    config = config_analizador(servicio)
    if not config.key:
        return None

    try:
        from azure.ai.contentunderstanding import ContentUnderstandingClient
        from azure.core.credentials import AzureKeyCredential

        client = ContentUnderstandingClient(
            endpoint=config.endpoint,
            credential=AzureKeyCredential(config.key),
            api_version=config.api_version,
        )
        with open(pdf_path, "rb") as f:
            file_bytes = f.read()
        poller = client.begin_analyze_binary(
            analyzer_id=config.analyzer_id,
            binary_input=file_bytes,
            content_type="application/pdf",
        )
        result = poller.result()
    except ImportError:
        # Falta el extra `cu` (azure-ai-contentunderstanding). Degradar.
        return None
    except Exception:
        # Error de red / credencial / archivo. Degradar sin romper la corrida.
        return None

    campos = extract_all_fields(result.as_dict())
    return ReciboExtraido.desde_campos(campos)
