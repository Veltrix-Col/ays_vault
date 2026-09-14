"""
Cliente de IA independiente del proveedor.

Proveedor se elige por variable de entorno GESTOR_LLM_PROVIDER:
  azure_foundry (por defecto) -> Azure AI Foundry, el mismo recurso que ya usa
                              `conciliador.sources.foundry_recibo` para leer recibos PDF
                              (AZURE_FOUNDRY_RECIBO_ENDPOINT / AZURE_FOUNDRY_RECIBO_KEY),
                              con un deployment propio para este flujo:
                              AZURE_FOUNDRY_COTIZACIONES_MODEL.
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
"""
from __future__ import annotations

import hashlib
import json
import os
import re
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
        `text.format.type = "json_schema"`. Sin `strict` (los schemas de este motor son
        anidados y no se escribieron pensando en el `additionalProperties`/`required`
        exhaustivo que exige el modo estricto); `core.adaptador.extraer_con_llm` ya
        descarta cualquier `cobertura_id`/`plan_id` que no exista en el catálogo, así que
        una respuesta no perfectamente estricta no compromete la validación."""
        if not self.model:
            raise RuntimeError(
                "Falta AZURE_FOUNDRY_COTIZACIONES_MODEL (deployment) en el entorno para "
                "usar el proveedor azure_foundry.")
        nombre_schema = re.sub(r"[^A-Za-z0-9_]", "_", nombre)[:64] or "extraccion"
        respuesta = self._cliente().responses.create(
            model=self.model,
            instructions=system,
            input=user,
            text={"format": {"type": "json_schema", "name": nombre_schema, "schema": schema}},
        )
        return json.loads(respuesta.output_text)
