from __future__ import annotations

import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from intranet_sso.provisioning import USERNAME_PREFIX


class MensajeViewTests(TestCase):
    def setUp(self):
        self.url = reverse("asistente_zoho:mensaje")
        # Username con el prefijo de intranet_sso: así se autentican en la
        # práctica quienes navegan Colectivos/SOAT/portal (sin MFA propio de
        # CardManager) -- ver vault.middleware.SecureSessionMiddleware, que
        # exige verificación MFA para cualquier otro usuario autenticado.
        self.user = get_user_model().objects.create_user(
            username=f"{USERNAME_PREFIX}analista_test",
            email="analista@segurosays.com",
        )

    def test_requiere_login(self):
        response = self.client.post(self.url, data="{}", content_type="application/json")
        self.assertEqual(response.status_code, 302)

    def test_rechaza_get(self):
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 405)

    def test_json_invalido(self):
        self.client.force_login(self.user)
        response = self.client.post(self.url, data="no-es-json", content_type="application/json")
        self.assertEqual(response.status_code, 400)

    def test_mensaje_vacio_es_rechazado(self):
        self.client.force_login(self.user)
        response = self.client.post(
            self.url, data=json.dumps({"mensaje": "   "}), content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    @patch("asistente_zoho.views.orchestrator.responder")
    def test_usa_el_correo_de_la_sesion_no_uno_del_payload(self, responder_mock):
        responder_mock.return_value = "Hola"
        self.client.force_login(self.user)
        response = self.client.post(
            self.url,
            data=json.dumps({
                "mensaje": "¿qué tareas tengo?",
                "historial": [{"role": "user", "content": "hola"}],
                "user_email": "otra-persona@segurosays.com",
            }),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"respuesta": "Hola"})
        responder_mock.assert_called_once_with(
            mensaje="¿qué tareas tengo?",
            historial=[{"role": "user", "content": "hola"}],
            user_email="analista@segurosays.com",
        )
