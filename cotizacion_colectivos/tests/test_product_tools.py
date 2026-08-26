from pathlib import Path
from types import SimpleNamespace

from django.test import SimpleTestCase, override_settings
from django.template.loader import render_to_string
from django.urls import reverse

from cotizacion_colectivos.adjustments import allowed_adjustments
from cotizacion_colectivos.services.task_publisher import (
    ColectivosTaskPayload,
    TaskPublishingDisabled,
    get_task_publisher,
)
from cotizacion_colectivos.modes import NOVELTIES_MODE, TOOL_MODES
from cotizacion_colectivos.forms import ClientSearchForm


class ProductToolsContractTests(SimpleTestCase):
    def test_current_novelties_are_only_entry_and_retirement(self):
        codes = {item.code for item in allowed_adjustments("91")}
        self.assertEqual(codes, {"SIN_CAMBIOS", "INCLUSION", "RETIRO"})

    def test_three_canonical_tool_routes_exist(self):
        self.assertEqual(reverse("cotizacion_colectivos:novelties_index"), "/cotizacion-colectivos/novedades/")
        self.assertEqual(reverse("cotizacion_colectivos:individual_index"), "/cotizacion-colectivos/cotizacion-individual/")
        self.assertEqual(reverse("cotizacion_colectivos:invitations_index"), "/cotizacion-colectivos/invitaciones-aseguradoras/")

    def test_novelties_template_exposes_collective_renewal_workspace_without_changing_manual_flow(self):
        templates = (
            Path("templates/cotizacion_colectivos/index.html"),
            Path("templates/cotizacion_colectivos/policy_detail.html"),
            Path("templates/cotizacion_colectivos/external/portal.html"),
            Path("templates/cotizacion_colectivos/external/_functional_entity.html"),
        )
        content = "\n".join(path.read_text(encoding="utf-8") for path in templates)
        self.assertIn("Renovaciones Colectivo", content)
        self.assertIn("Próximos envíos", content)
        self.assertNotIn("Próximas a vencer", content)
        self.assertIn("Próximos a enviar", content)
        self.assertIn("Seguimiento de links", content)
        self.assertIn("novelties-workspace-layout", content)
        self.assertIn("novelties-workspace-sidebar", content)
        self.assertIn("novelties_client_search", content)
        self.assertIn("Periodo: {{ renewal_target_period_label }}", content)
        for label in ("Programadas", "Enviadas", "Respondidas", "En alerta", "Con error"):
            if label == "Programadas":
                self.assertNotIn(label, content)
            else:
                self.assertIn(label, content)
        self.assertNotIn("Ver código", content)
        self.assertNotIn("Fecha efectiva", content)
        self.assertIn("Fecha solicitada de ingreso", content)
        self.assertIn("Fecha solicitada de retiro", content)

        html = render_to_string("cotizacion_colectivos/index.html", {
            "colectivos_mode": TOOL_MODES[NOVELTIES_MODE],
            "form": ClientSearchForm(),
            "zoho_environment": SimpleNamespace(css_class="sandbox", label="Sandbox"),
            "colectivos_navigation": {},
            "renewal_cycles": (),
            "renewal_tab": "upcoming",
            "renewal_dashboard": {},
            "renewal_target_period_label": "Septiembre 2026",
            "renewal_sync_error": "",
            "messages": (),
        })
        self.assertNotIn("El perfil se cambia mediante", html)
        self.assertNotIn("cliente → póliza → ingreso o retiro → enlace → respuesta", html)
        self.assertIn("Renovaciones Colectivo", html)
        self.assertIn("Buscar cliente", html)
        self.assertIn("Próximos a enviar", html)
        self.assertIn("Seguimiento de links", html)
        self.assertIn("Periodo: Septiembre 2026", html)

    @override_settings(COLECTIVOS_TASK_PUBLISH_ENABLED=False)
    def test_task_publisher_disabled_configuration_is_fail_closed(self):
        publisher = get_task_publisher()
        self.assertFalse(publisher.enabled)
        payload = ColectivosTaskPayload(
            request_kind="NOVEDAD",
            source_kind="company",
            policy_context="opaque-local-context",
            branch_code="91",
            local_reference="local-reference",
        )
        with self.assertRaises(TaskPublishingDisabled):
            publisher.publish(payload)
