"""
Cliente de IA independiente del proveedor.

Proveedor se elige por variable de entorno GESTOR_LLM_PROVIDER:
  azure_foundry (por defecto) -> Azure AI Foundry, el mismo recurso que ya usa
                              `conciliador.sources.foundry_recibo` para leer recibos PDF
                              (AZURE_FOUNDRY_RECIBO_ENDPOINT / AZURE_FOUNDRY_RECIBO_KEY),
                              con un deployment propio por flujo (ver `model` en cada
                              caso de uso, p. ej. AZURE_FOUNDRY_COTIZACIONES_MODEL).
  bedrock                   -> usa credenciales AWS estándar (AWS_PROFILE / AWS_ACCESS_KEY_ID / rol IAM)
                              y AWS_REGION. Modelo por GESTOR_LLM_MODEL (id de Bedrock).
  anthropic                 -> ANTHROPIC_API_KEY
  vertex                    -> credenciales GCP (ANTHROPIC_VERTEX_PROJECT_ID, CLOUD_ML_REGION)
  dry-run                   -> no llama a nada; escribe el prompt en out/prompts/ y devuelve vacío.
                              Útil para revisar qué se le pide al modelo y para pruebas sin credenciales.

Toda extracción pide salida estructurada validable contra un JSON schema (tool-use en
Anthropic; Structured Outputs en Azure/OpenAI). Los resultados se cachean por hash del
prompt en out/cache_llm/ (así re-ejecutar el CLI no vuelve a cobrar tokens ni cambia los
valores que el analista ya revisó).

Módulo compartido: originalmente vivía en `gestor_cotizaciones/core/llm.py`; se movió
aquí (`integrations/llm`) para que cualquier feature de IA de AyS Vault (gestor de
cotizaciones, asistente de consulta Zoho, etc.) use el mismo cliente por proveedor en
vez de duplicarlo.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

DEFAULT_MODELS = {
    "bedrock": "anthropic.claude-sonnet-4-5-20250929-v1:0",   # ajustar al id disponible en su región
    "anthropic": "claude-sonnet-4-5",
    "vertex": "claude-sonnet-4-5@20250929",
}


class LLM:
    def __init__(self, provider: str | None = None, model: str | None = None,
                 cache_dir: str | Path = "out/cache_llm", prompt_dir: str | Path = "out/prompts"):
        self.provider = (provider or os.getenv("GESTOR_LLM_PROVIDER", "azure_foundry")).lower()
        if model:
            self.model = model
        elif self.provider == "azure_foundry":
            # Deployment propio de este flujo: no se reutiliza AZURE_FOUNDRY_RECIBO_MODEL
            # (ese es el afinado para los 6 campos de un recibo), aunque sí el mismo
            # endpoint/key -- ver `_cliente`.
            self.model = os.getenv("AZURE_FOUNDRY_COTIZACIONES_MODEL", "")
        else:
            self.model = os.getenv("GESTOR_LLM_MODEL") or DEFAULT_MODELS.get(self.provider, "")
        self.cache_dir = Path(cache_dir)
        self.prompt_dir = Path(prompt_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.prompt_dir.mkdir(parents=True, exist_ok=True)
        self._client = None

    # ---------- cliente por proveedor ----------
    def _cliente(self):
        if self._client is not None:
            return self._client
        if self.provider == "azure_foundry":
            from openai import OpenAI
            endpoint = os.getenv("AZURE_FOUNDRY_RECIBO_ENDPOINT")
            key = os.getenv("AZURE_FOUNDRY_RECIBO_KEY")
            if not endpoint or not key:
                raise RuntimeError(
                    "Faltan AZURE_FOUNDRY_RECIBO_ENDPOINT/AZURE_FOUNDRY_RECIBO_KEY en el "
                    "entorno para usar el proveedor azure_foundry.")
            self._client = OpenAI(base_url=endpoint, api_key=key)
        elif self.provider == "bedrock":
            from anthropic import AnthropicBedrock
            self._client = AnthropicBedrock(aws_region=os.getenv("AWS_REGION", "us-east-1"))
        elif self.provider == "anthropic":
            from anthropic import Anthropic
            self._client = Anthropic()
        elif self.provider == "vertex":
            from anthropic import AnthropicVertex
            self._client = AnthropicVertex()
        else:
            raise ValueError(f"Proveedor LLM desconocido: {self.provider}")
        return self._client

    # ---------- llamada estructurada ----------
    def extraer(self, *, nombre: str, system: str, user: str, schema: dict[str, Any],
                max_tokens: int = 8000) -> dict[str, Any]:
        """
        Pide al modelo una extracción estructurada que cumpla `schema` y devuelve el dict
        ya deserializado. El mecanismo concreto (tool-use vs Structured Outputs) lo decide
        el proveedor; el resultado siempre tiene el mismo shape para quien llama.
        """
        clave = hashlib.sha256(f"{self.model}|{system}|{user}|{json.dumps(schema, sort_keys=True)}".encode()).hexdigest()[:24]
        cache = self.cache_dir / f"{nombre}_{clave}.json"
        if cache.exists():
            return json.loads(cache.read_text(encoding="utf-8"))

        (self.prompt_dir / f"{nombre}_{clave}.md").write_text(
            f"# system\n{system}\n\n# user\n{user}\n\n# schema\n```json\n{json.dumps(schema, ensure_ascii=False, indent=2)}\n```",
            encoding="utf-8")

        if self.provider == "dry-run":
            return {}

        if self.provider == "azure_foundry":
            data = self._extraer_azure_foundry(nombre=nombre, system=system, user=user, schema=schema)
        else:
            data = self._extraer_anthropic(system=system, user=user, schema=schema, max_tokens=max_tokens)

        cache.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return data

    def _extraer_anthropic(self, *, system: str, user: str, schema: dict[str, Any],
                            max_tokens: int) -> dict[str, Any]:
        """bedrock | anthropic | vertex: "tool use" con un JSON schema como `input_schema`."""
        tool = {"name": "registrar_extraccion", "description": "Registra la extracción estructurada.",
                "input_schema": schema}
        resp = self._cliente().messages.create(
            model=self.model, max_tokens=max_tokens, system=system,
            tools=[tool], tool_choice={"type": "tool", "name": "registrar_extraccion"},
            messages=[{"role": "user", "content": user}],
        )
        for block in resp.content:
            if getattr(block, "type", "") == "tool_use":
                return block.input
        raise RuntimeError("El modelo no devolvió una llamada a herramienta.")

    def _extraer_azure_foundry(self, *, nombre: str, system: str, user: str,
                                schema: dict[str, Any]) -> dict[str, Any]:
        """Azure AI Foundry vía SDK `openai` (mismo patrón que
        `conciliador.sources.foundry_recibo.extraer_recibo`): Structured Outputs con
        `text.format.type = "json_schema"`. Azure exige `additionalProperties: false`
        en todo objeto del schema aunque no se pida `strict` (probado en vivo: sin esto
        responde 400 `invalid_json_schema`) -- se agrega automáticamente aquí para que
        quien escriba un `_SCHEMA` en un adaptador/ramo nuevo no tenga que saberlo."""
        if not self.model:
            raise RuntimeError(
                "Falta AZURE_FOUNDRY_COTIZACIONES_MODEL (deployment) en el entorno para "
                "usar el proveedor azure_foundry.")
        nombre_schema = re.sub(r"[^A-Za-z0-9_]", "_", nombre)[:64] or "extraccion"
        schema_azure = _sin_propiedades_extra(schema)
        respuesta = self._cliente().responses.create(
            model=self.model,
            instructions=system,
            input=user,
            text={"format": {"type": "json_schema", "name": nombre_schema, "schema": schema_azure}},
        )
        return json.loads(respuesta.output_text)

    # ---------- conversación corta con herramientas (sin caché, sin persistencia) ----------
    def conversar(
        self, *, system: str, mensajes: list[dict[str, str]], herramientas: list[dict[str, Any]],
        ejecutar_tool: Callable[[str, dict[str, Any]], str], max_tool_iterations: int = 4,
        max_output_tokens: int = 1200,
    ) -> str:
        """Un turno de una conversación corta que puede invocar `herramientas` de solo
        lectura antes de responder. No cachea (cada mensaje del usuario es distinto por
        diseño) y no persiste nada: quien llama es responsable de guardar/recortar el
        historial fuera de este método (p. ej. en el propio navegador).

        `herramientas`: lista de `{"name", "description", "parameters"}` (JSON schema).
        `ejecutar_tool(nombre, argumentos) -> str`: ejecuta la tool y devuelve su
        resultado ya serializado (normalmente `json.dumps(...)`); cualquier excepción
        debe resolverla quien la implementa (nunca debe propagar acá).
        """
        if self.provider != "azure_foundry":
            raise NotImplementedError(
                "conversar() sólo está implementado para el proveedor azure_foundry por ahora.")
        return self._conversar_azure_foundry(
            system=system, mensajes=mensajes, herramientas=herramientas,
            ejecutar_tool=ejecutar_tool, max_tool_iterations=max_tool_iterations,
            max_output_tokens=max_output_tokens,
        )

    def _conversar_azure_foundry(
        self, *, system: str, mensajes: list[dict[str, str]], herramientas: list[dict[str, Any]],
        ejecutar_tool: Callable[[str, dict[str, Any]], str], max_tool_iterations: int,
        max_output_tokens: int,
    ) -> str:
        """Responses API de Azure AI Foundry con function calling.

        NOTA: a diferencia de `_extraer_azure_foundry` (Structured Outputs, probado en
        vivo), este camino todavía no se ha ejecutado contra un deployment real -- la
        forma de los `tools`/`function_call`/`function_call_output` está tomada de los
        tipos del SDK `openai` instalado (`openai/types/responses/*`), no de una prueba
        en vivo. Confirmar contra sandbox antes de depender de esto en producción.
        """
        if not self.model:
            raise RuntimeError(
                "Falta AZURE_FOUNDRY_COTIZACIONES_MODEL (deployment) en el entorno para "
                "usar el proveedor azure_foundry.")
        cliente = self._cliente()
        tools = [
            {
                "type": "function",
                "name": herramienta["name"],
                "description": herramienta.get("description", ""),
                "parameters": herramienta["parameters"],
                "strict": False,
            }
            for herramienta in herramientas
        ]
        respuesta = cliente.responses.create(
            model=self.model, instructions=system, input=list(mensajes),
            tools=tools, max_output_tokens=max_output_tokens,
        )
        for _ in range(max_tool_iterations):
            llamadas = [item for item in respuesta.output if getattr(item, "type", "") == "function_call"]
            if not llamadas:
                return respuesta.output_text
            salidas = []
            for llamada in llamadas:
                try:
                    argumentos = json.loads(llamada.arguments or "{}")
                except json.JSONDecodeError:
                    argumentos = {}
                resultado = ejecutar_tool(llamada.name, argumentos)
                salidas.append({
                    "type": "function_call_output",
                    "call_id": llamada.call_id,
                    "output": resultado,
                })
            respuesta = cliente.responses.create(
                model=self.model, instructions=system, tools=tools,
                max_output_tokens=max_output_tokens,
                previous_response_id=respuesta.id, input=salidas,
            )
        # Tope de iteraciones alcanzado: se devuelve lo último que haya respondido el
        # modelo (puede ser vacío si sólo encadenaba tool calls) en vez de reventar la
        # conversación.
        return respuesta.output_text


def _sin_propiedades_extra(nodo: Any) -> Any:
    """Copia `nodo` normalizando todo objeto JSON schema al formato que exige
    Azure/OpenAI Structured Outputs (probado en vivo, aplica aunque no se pida
    `strict`): `additionalProperties: false`, y `required` con TODAS las claves
    de `properties` (una clave puede seguir siendo "opcional" en la práctica si
    su `type` incluye `null` -- el modelo la manda en `null` en vez de omitirla).
    Recursivo: respeta `properties`, `items` y uniones como `anyOf`/`oneOf`/`allOf`."""
    if isinstance(nodo, dict):
        copia = {clave: _sin_propiedades_extra(valor) for clave, valor in nodo.items()}
        if copia.get("type") == "object" and "properties" in copia:
            copia.setdefault("additionalProperties", False)
            copia["required"] = list(copia["properties"].keys())
        return copia
    if isinstance(nodo, list):
        return [_sin_propiedades_extra(valor) for valor in nodo]
    return nodo
