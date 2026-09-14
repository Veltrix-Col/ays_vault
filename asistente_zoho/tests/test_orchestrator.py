from __future__ import annotations

import json
from unittest.mock import patch

from django.test import SimpleTestCase

from asistente_zoho import orchestrator
from cotizacion_colectivos.services.common import ColectivosServiceError


class BuildToolExecutorTests(SimpleTestCase):
    @patch("asistente_zoho.orchestrator.tools")
    def test_mis_tareas_ignora_correo_del_modelo_y_usa_el_de_sesion(self, tools_mock):
        tools_mock.mis_tareas.return_value = {"total": 0, "tareas": []}
        ejecutar = orchestrator.build_tool_executor(user_email="real@segurosays.com")

        ejecutar("mis_tareas", {"correo_usuario": "otra-persona@segurosays.com"})

        tools_mock.mis_tareas.assert_called_once_with("real@segurosays.com")

    @patch("asistente_zoho.orchestrator.tools")
    def test_error_de_negocio_se_serializa_con_su_mensaje(self, tools_mock):
        tools_mock.buscar_cliente.side_effect = ColectivosServiceError("not_found", "No existe ese cliente.")
        ejecutar = orchestrator.build_tool_executor(user_email="a@b.com")

        salida = ejecutar("buscar_cliente", {"query": "x"})

        self.assertEqual(json.loads(salida), {"error": "No existe ese cliente."})

    @patch("asistente_zoho.orchestrator.tools")
    def test_error_inesperado_no_propaga_detalle_interno(self, tools_mock):
        tools_mock.obtener_poliza.side_effect = RuntimeError("detalle interno sensible")
        ejecutar = orchestrator.build_tool_executor(user_email="a@b.com")

        salida = ejecutar("obtener_poliza", {"numero_poliza": "1"})

        cuerpo = json.loads(salida)
        self.assertNotIn("detalle interno sensible", cuerpo["error"])

    def test_tool_desconocida(self):
        ejecutar = orchestrator.build_tool_executor(user_email="a@b.com")
        salida = ejecutar("borrar_todo", {})
        self.assertEqual(json.loads(salida), {"error": "Herramienta desconocida."})


class ResponderTests(SimpleTestCase):
    def test_mensaje_vacio_no_llama_al_modelo(self):
        with patch("asistente_zoho.orchestrator.LLM") as llm_cls:
            respuesta = orchestrator.responder(mensaje="   ", historial=[], user_email="a@b.com")
        self.assertIn("Escribe una pregunta", respuesta)
        llm_cls.assert_not_called()

    def test_recorta_historial_a_los_ultimos_turnos(self):
        historial_largo = [{"role": "user", "content": f"m{i}"} for i in range(20)]
        with patch("asistente_zoho.orchestrator.LLM") as llm_cls:
            llm_cls.return_value.conversar.return_value = "ok"
            orchestrator.responder(mensaje="hola", historial=historial_largo, user_email="a@b.com")
        mensajes_enviados = llm_cls.return_value.conversar.call_args.kwargs["mensajes"]
        self.assertLessEqual(len(mensajes_enviados), orchestrator.MAX_HISTORY_MESSAGES + 1)

    def test_falla_del_modelo_devuelve_mensaje_generico(self):
        with patch("asistente_zoho.orchestrator.LLM") as llm_cls:
            llm_cls.return_value.conversar.side_effect = RuntimeError("boom")
            respuesta = orchestrator.responder(mensaje="hola", historial=[], user_email="a@b.com")
        self.assertIn("No fue posible consultar el asistente", respuesta)
