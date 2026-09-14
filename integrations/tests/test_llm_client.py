from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import Mock

from django.test import SimpleTestCase

from integrations.llm import LLM


def _function_call(call_id: str, name: str, arguments: dict) -> SimpleNamespace:
    return SimpleNamespace(
        type="function_call", call_id=call_id, name=name,
        arguments=json.dumps(arguments),
    )


class ConversarAzureFoundryTests(SimpleTestCase):
    def _llm(self) -> LLM:
        llm = LLM(provider="azure_foundry", model="deployment-de-prueba")
        llm._client = Mock()
        return llm

    def test_responde_directo_sin_tool_calls(self):
        llm = self._llm()
        llm._client.responses.create.return_value = SimpleNamespace(
            id="resp_1", output=[], output_text="Hola, ¿en qué te ayudo?",
        )
        respuesta = llm.conversar(
            system="system", mensajes=[{"role": "user", "content": "hola"}],
            herramientas=[], ejecutar_tool=lambda nombre, args: "{}",
        )
        self.assertEqual(respuesta, "Hola, ¿en qué te ayudo?")
        llm._client.responses.create.assert_called_once()

    def test_ejecuta_tool_y_continua_la_conversacion(self):
        llm = self._llm()
        primera = SimpleNamespace(
            id="resp_1",
            output=[_function_call("call_1", "buscar_cliente", {"query": "Acme"})],
            output_text="",
        )
        segunda = SimpleNamespace(id="resp_2", output=[], output_text="Encontré a Acme S.A.S.")
        llm._client.responses.create.side_effect = [primera, segunda]
        ejecutar_tool = Mock(return_value=json.dumps({"nombre": "Acme S.A.S."}))

        respuesta = llm.conversar(
            system="system", mensajes=[{"role": "user", "content": "busca Acme"}],
            herramientas=[{"name": "buscar_cliente", "description": "", "parameters": {}}],
            ejecutar_tool=ejecutar_tool,
        )

        self.assertEqual(respuesta, "Encontré a Acme S.A.S.")
        ejecutar_tool.assert_called_once_with("buscar_cliente", {"query": "Acme"})
        segunda_llamada = llm._client.responses.create.call_args_list[1].kwargs
        self.assertEqual(segunda_llamada["previous_response_id"], "resp_1")
        self.assertEqual(segunda_llamada["input"], [
            {"type": "function_call_output", "call_id": "call_1", "output": json.dumps({"nombre": "Acme S.A.S."})},
        ])

    def test_corta_en_el_tope_de_iteraciones_sin_reventar(self):
        llm = self._llm()
        siempre_pide_tool = SimpleNamespace(
            id="resp_x",
            output=[_function_call("call_x", "buscar_cliente", {})],
            output_text="",
        )
        llm._client.responses.create.return_value = siempre_pide_tool

        respuesta = llm.conversar(
            system="system", mensajes=[{"role": "user", "content": "?"}],
            herramientas=[{"name": "buscar_cliente", "description": "", "parameters": {}}],
            ejecutar_tool=lambda nombre, args: "{}", max_tool_iterations=2,
        )

        self.assertEqual(respuesta, "")
        self.assertEqual(llm._client.responses.create.call_count, 3)

    def test_proveedor_no_soportado_falla_explicitamente(self):
        llm = LLM(provider="anthropic", model="claude-sonnet-4-5")
        with self.assertRaises(NotImplementedError):
            llm.conversar(
                system="system", mensajes=[], herramientas=[],
                ejecutar_tool=lambda nombre, args: "{}",
            )
