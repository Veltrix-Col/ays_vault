"""Adapter de Azure AI Foundry: convierte un recibo/factura en PDF de SURA en
un DTO plano (`ReciboExtraido`) que las reglas consumen sin saber nada de
Foundry ni de la extracción de texto.

Reemplaza al antiguo adapter de Azure Content Understanding (un analyzer
distinto por servicio -- salud/vida/movilidad). Ahora hay UN solo modelo
compartido para los 3 servicios: se le manda el texto del PDF (extraido con
PyMuPDF, no el binario, para ahorrar tokens) junto con un prompt fijo que ya
sabe reconocer las variantes de layout de SURA, y responde JSON estructurado
(Structured Outputs) con 6 campos fijos.

Es el UNICO modulo que habla con Foundry. El SDK (`openai`) y `pymupdf` se
importan de forma perezosa dentro de `extraer_recibo`, para que instalar el
paquete base y correr los tests no requiera credenciales -- las reglas se
prueban con un `ReciboExtraido` fabricado, igual que antes con Content
Understanding.

Configuracion (una sola, compartida por los 3 servicios). La KEY SIEMPRE se
lee de variable de entorno (nunca vive en el repo); endpoint/modelo tienen un
default no secreto sobreescribible por env:

    AZURE_FOUNDRY_RECIBO_KEY       (obligatoria para llamar; su ausencia degrada a None)
    AZURE_FOUNDRY_RECIBO_ENDPOINT  (opcional; si no, usa el default del recurso de desarrollo)
    AZURE_FOUNDRY_RECIBO_MODEL     (opcional; si no, usa el deployment de desarrollo)
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime

# Endpoint / modelo por defecto (NO secretos): el recurso/deployment de
# desarrollo. En produccion se puede sobreescribir por env si cambia.
_ENDPOINT_DEV = "https://ays-cu-renovaciones-dev.services.ai.azure.com/openai/v1"
_MODEL_DEV = "gpt-5.6-luna"

RECIBO_PROMPT = """# ROL
Eres un asistente experto en extracción estructurada de datos desde documentos de seguros emitidos por Suramericana (SURA) en Colombia (pólizas, cotizaciones y recibos de cobro en PDF).

# CONTEXTO
Vas a recibir el texto extraído de un PDF de SURA. Existen varias variantes de layout, y DEBES reconocer los valores de cobro sin importar cuál etiqueta use el documento:

**Variante A — Cotización/carátula con cobro embebido:**
No tiene sección "INFORMACIÓN DEL RECIBO" ni "Número de recibo" explícito, pero SÍ trae "Valor sin IVA", "Valor IVA", "Valor total del Seguro" junto con "Número de documento" y la leyenda "Documento de: COBRO A FAVOR DE SURAMERICANA". Esto SIGUE SIENDO un cobro válido; usa el "Número de documento" como `numero_recibo`.

**Variante B/C — Recibo de cobro:**
Trae "INFORMACIÓN DEL RECIBO" con "Número de recibo", "Valor sin IVA", "Valor IVA", "Total a pagar". Puede incluir una tabla "COBERTURAS" con varias filas — ignórala, el total va siempre en "Total a pagar", no en la suma de coberturas individuales.

**Variante D — Recibo de Autos/Generales (multi-página):**
Página 1 trae "Número del recibo", "Valor sin Iva", "Valor IVA", "Total a pagar". Página 2 trae "LISTADO DE RIESGOS" — ignóralo por completo.

Todos los documentos pueden incluir tablas largas de "RELACIÓN DE ASEGURADOS" o "LISTADO DE RIESGOS" que NUNCA deben extraerse.

# SINÓNIMOS DE CAMPOS (mapeo obligatorio)
Reconoce estas variaciones como el MISMO campo, sin importar mayúsculas, tildes o espacios:
- `numero_poliza` ← "Número de la póliza", "Número de póliza"
- `numero_recibo` ← "Número de recibo", "Número del recibo". Si no existe ninguno de estos pero sí hay "Número de documento" junto a valores de cobro, úsalo como respaldo.
- `valor_sin_iva` ← "Valor sin IVA", "Valor sin Iva"
- `valor_iva` ← "Valor IVA", "Valor Iva"
- `valor_total_a_pagar` ← "Total a pagar", "Valor total del Seguro", "Valor total a pagar" (el total consolidado del recibo, nunca el valor de una cobertura individual ni de un riesgo/placa individual)
- `fecha_expedicion` ← "Fecha de expedición", "Fecha de expedición del recibo", "Fecha de emisión", "Fecha de generación del documento". Es la fecha en que la aseguradora expidió/generó ESTE recibo o documento de cobro, nunca la fecha de inicio/fin de vigencia de la póliza ni la fecha de pago.

# TAREA
Extrae ÚNICAMENTE estos 6 campos:
1. `numero_poliza`
2. `numero_recibo`
3. `valor_sin_iva`
4. `valor_iva`
5. `valor_total_a_pagar`
6. `fecha_expedicion`

Reglas:
- Antes de marcar un campo como `null`, revisa TODAS sus variantes de nombre listadas arriba. No asumas ausencia solo porque falta el nombre de sección "esperado".
- Los valores monetarios se devuelven como número (sin `$`, sin puntos ni comas de miles), usando punto solo si hay decimales.
- `fecha_expedicion` se devuelve siempre en formato `YYYY-MM-DD`. Si el documento la trae en otro formato (p. ej. "15/03/2026" o "15 de marzo de 2026"), conviértela a `YYYY-MM-DD`.
- No extraigas nada más: ni tomador, ni vigencia, ni coberturas, ni periodo de cobro, ni tablas de asegurados/riesgos.
- Si el documento tiene varias páginas, los 6 valores casi siempre están en la primera página (encabezado del recibo); no busques en el detalle de asegurados/riesgos.
- Si un campo genuinamente no aparece bajo ninguna variante, usa `null`.

# FORMATO DE SALIDA
Responde ÚNICAMENTE con un JSON válido, sin texto adicional ni explicaciones, con esta estructura exacta:

```json
{
  "numero_poliza": "string | null",
  "numero_recibo": "string | null",
  "valor_sin_iva": number | null,
  "valor_iva": number | null,
  "valor_total_a_pagar": number | null,
  "fecha_expedicion": "string (YYYY-MM-DD) | null"
}
```"""

_RESPUESTA_SCHEMA = {
    "type": "object",
    "properties": {
        "numero_poliza": {"type": ["string", "null"]},
        "numero_recibo": {"type": ["string", "null"]},
        "valor_sin_iva": {"type": ["number", "null"]},
        "valor_iva": {"type": ["number", "null"]},
        "valor_total_a_pagar": {"type": ["number", "null"]},
        "fecha_expedicion": {"type": ["string", "null"]},
    },
    "required": [
        "numero_poliza", "numero_recibo", "valor_sin_iva", "valor_iva",
        "valor_total_a_pagar", "fecha_expedicion",
    ],
    "additionalProperties": False,
}


# ---------------------------------------------------------------------------
# DTO plano del recibo (lo que consume la regla)
# ---------------------------------------------------------------------------
def _a_float(valor) -> float | None:
    """Convierte a float tolerando strings con separadores. `None` se
    preserva (el modelo ya normaliza el numero; si dice null, es null)."""
    if valor is None:
        return None
    if isinstance(valor, (int, float)):
        return float(valor)
    texto = str(valor).strip()
    if not texto:
        return None
    texto = texto.replace("$", "").replace(" ", "")
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
        return None


def _a_fecha_iso(valor) -> str | None:
    """Valida que el modelo haya devuelto una fecha real en `YYYY-MM-DD`
    (el formato que ya usan las escrituras a campos `date` de Zoho, ver
    `SYNTHETIC_TEST_TASK` en `cotizacion_colectivos.services.task_publisher`).
    Cualquier otro formato se descarta a `None` en vez de reenviarse tal cual
    a Zoho: un valor de fecha invalido rechaza la escritura del campo completo."""
    if not valor:
        return None
    texto = str(valor).strip()
    try:
        datetime.strptime(texto, "%Y-%m-%d")
    except ValueError:
        return None
    return texto


@dataclass(frozen=True)
class ReciboExtraido:
    """Los 6 campos que extrae el modelo de un recibo/cobro de SURA."""

    numero_poliza: str | None
    numero_recibo: str | None
    valor_sin_iva: float | None
    valor_iva: float | None
    valor_total_a_pagar: float | None
    fecha_expedicion: str | None  # YYYY-MM-DD

    @classmethod
    def desde_json(cls, campos: dict) -> "ReciboExtraido":
        """Construye el DTO desde el dict ya deserializado de la respuesta del modelo."""
        numero_poliza = campos.get("numero_poliza")
        numero_recibo = campos.get("numero_recibo")
        return cls(
            numero_poliza=str(numero_poliza).strip() if numero_poliza else None,
            numero_recibo=str(numero_recibo).strip() if numero_recibo else None,
            valor_sin_iva=_a_float(campos.get("valor_sin_iva")),
            valor_iva=_a_float(campos.get("valor_iva")),
            valor_total_a_pagar=_a_float(campos.get("valor_total_a_pagar")),
            fecha_expedicion=_a_fecha_iso(campos.get("fecha_expedicion")),
        )


# ---------------------------------------------------------------------------
# Configuracion (compartida por los 3 servicios: salud, vida, movilidad)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ConfigFoundryRecibo:
    endpoint: str
    modelo: str
    key: str | None  # None => sin credencial en env; `extraer_recibo` degrada a None


def config_foundry_recibo() -> ConfigFoundryRecibo:
    """Resuelve la configuracion del modelo de Foundry desde variables de
    entorno, con defaults no secretos para endpoint/modelo."""
    return ConfigFoundryRecibo(
        endpoint=os.environ.get("AZURE_FOUNDRY_RECIBO_ENDPOINT", _ENDPOINT_DEV),
        modelo=os.environ.get("AZURE_FOUNDRY_RECIBO_MODEL", _MODEL_DEV),
        key=os.environ.get("AZURE_FOUNDRY_RECIBO_KEY"),
    )


# ---------------------------------------------------------------------------
# Llamada a Foundry (import perezoso de openai/pymupdf)
# ---------------------------------------------------------------------------
def extraer_texto_pdf(pdf_path) -> str:
    """Extrae el texto de un PDF con PyMuPDF, en orden de lectura visual.

    Nota: los PDF de SURA traen tildes/eñes con un mapeo de fuente roto (se
    extraen como '�'); los valores numericos y montos no se ven
    afectados. El modelo reconoce las etiquetas igual, por eso no se intenta
    corregir el encoding aqui."""
    import pymupdf

    with pymupdf.open(pdf_path) as doc:
        return "\n".join(pagina.get_text() for pagina in doc)


def extraer_recibo(pdf_path) -> ReciboExtraido | None:
    """Envia el texto del PDF al modelo de Foundry y devuelve el recibo extraido.

    Degrada a `None` (sin romper la conciliacion) si: no hay ruta de PDF, el
    archivo no existe, no hay KEY en env, o el SDK no esta instalado / la
    llamada falla. La regla que consume esto traduce el `None` a un
    incidente 'N/D' visible en el reporte."""
    if not pdf_path or not os.path.exists(pdf_path):
        return None

    config = config_foundry_recibo()
    if not config.key:
        return None

    try:
        from openai import OpenAI

        texto = extraer_texto_pdf(pdf_path)
        client = OpenAI(base_url=config.endpoint, api_key=config.key)
        response = client.responses.create(
            model=config.modelo,
            instructions=RECIBO_PROMPT,
            input=texto,
            text={
                "format": {
                    "type": "json_schema",
                    "name": "recibo_sura",
                    "schema": _RESPUESTA_SCHEMA,
                    "strict": True,
                }
            },
        )
        campos = json.loads(response.output_text)
    except ImportError:
        # Falta el paquete `openai` o `pymupdf`. Degradar.
        return None
    except Exception:
        # Error de red / credencial / archivo / JSON invalido. Degradar sin romper la corrida.
        return None

    return ReciboExtraido.desde_json(campos)
