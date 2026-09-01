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
            Path("templates/cotizacion_colectivos/_renewal_panel_nav.html"),
            Path("templates/cotizacion_colectivos/renewal_tracking.html"),
            Path("templates/cotizacion_colectivos/_quick_tools_nav.html"),
            Path("templates/cotizacion_colectivos/policy_detail.html"),
            Path("templates/cotizacion_colectivos/external/portal.html"),
            Path("templates/cotizacion_colectivos/external/_functional_entity.html"),
        )
        content = "\n".join(path.read_text(encoding="utf-8") for path in templates)
        self.assertIn("Renovaciones Colectivo", content)
        self.assertIn("Próximos envíos", content)
        self.assertNotIn("Próximas a vencer", content)
        self.assertIn("Seguimiento de links", content)
        self.assertIn("renewal-panel-nav", content)
        self.assertIn("novelties-header-layout", content)
        self.assertIn("novelties-search-utility", content)
        self.assertIn("novelties-workspace-layout", content)
        self.assertIn("novelties-workspace-main", content)
        self.assertIn("renewal-dashboard", content)
        self.assertNotIn("novelties-workspace-sidebar", content)
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
        self.assertIn("Próximos envíos", html)
        self.assertIn("Próximos a enviar", html)
        self.assertIn("Seguimiento de links", html)
        self.assertIn("renewal-panel-nav", html)
        self.assertIn("Periodo: Septiembre 2026", html)

    def test_renewal_tracking_uses_human_period_separator(self):
        content = Path("templates/cotizacion_colectivos/renewal_tracking.html").read_text(encoding="utf-8")
        self.assertIn('{{ cycle.masked_policy }} / <span class="muted">{{ cycle.monthly_period_label }}</span>', content)
        self.assertNotIn("{{ cycle.monthly_period }}", content)

    def test_upcoming_table_places_vendedor_between_branch_and_frequency(self):
        content = Path("templates/cotizacion_colectivos/index.html").read_text(encoding="utf-8")
        self.assertIn("<th scope=\"col\">Ramo</th><th scope=\"col\">Vendedor</th><th scope=\"col\">Periodicidad</th>", content)
        html = render_to_string("cotizacion_colectivos/index.html", {
            "colectivos_mode": TOOL_MODES[NOVELTIES_MODE],
            "form": ClientSearchForm(), "zoho_environment": SimpleNamespace(css_class="sandbox", label="Sandbox"),
            "colectivos_navigation": {}, "renewal_cycles": (SimpleNamespace(
                policy_access_token="token", masked_policy="0400", client_label="Empresa",
                branch_name="VG deudores", seller_label="Vendedor Uno", payment_frequency="Mensual",
                recipient_email="qa@example.test", scheduled_for=None,
                get_status_display=lambda: "Programado",
            ),), "renewal_tab": "upcoming", "renewal_dashboard": {},
            "renewal_target_period_label": "Septiembre 2026", "renewal_sync_error": "", "messages": (),
        })
        self.assertIn("Vendedor Uno", html)
        empty_html = render_to_string("cotizacion_colectivos/index.html", {
            "colectivos_mode": TOOL_MODES[NOVELTIES_MODE],
            "form": ClientSearchForm(), "zoho_environment": SimpleNamespace(css_class="sandbox", label="Sandbox"),
            "colectivos_navigation": {}, "renewal_cycles": (SimpleNamespace(
                policy_access_token="token", masked_policy="0400", client_label="Empresa",
                branch_name="VG deudores", seller_label="", payment_frequency="Mensual",
                recipient_email="qa@example.test", scheduled_for=None,
                get_status_display=lambda: "Programado",
            ),), "renewal_tab": "upcoming", "renewal_dashboard": {},
            "renewal_target_period_label": "Septiembre 2026", "renewal_sync_error": "", "messages": (),
        })
        self.assertIn("—", empty_html)
        self.assertNotIn(">None<", empty_html)

    def test_monthly_switch_is_interactive_only_for_authorized_context(self):
        template = "cotizacion_colectivos/_renewal_panel_nav.html"
        base = {
            "renewal_tab": "upcoming",
            "monthly_renewals_enabled": False,
        }
        authorized = render_to_string(template, {
            **base,
            "can_manage_renewal_automation": True,
            "can_manage_notifications": False,
        })
        self.assertIn('method="post"', authorized)
        self.assertIn('name="enabled" value="1"', authorized)
        self.assertIn("OFF · Automatización desactivada", authorized)
        self.assertIn(f'action="{reverse("cotizacion_colectivos:monthly_renewals_toggle")}"', authorized)

        unauthorized = render_to_string(template, {
            **base,
            "can_manage_renewal_automation": False,
            "can_manage_notifications": True,
        })
        self.assertNotIn('method="post"', unauthorized)
        self.assertIn("Automatización: desactivada", unauthorized)
        self.assertIn("Activar automatización mensual", authorized)
        self.assertIn("Cancelar", authorized)

    def test_collective_request_task_uses_responsible_selection_modal(self):
        content = Path("templates/cotizacion_colectivos/request_detail.html").read_text(encoding="utf-8")
        self.assertIn('data-dialog-open="task-create-dialog-{{ task.outbox_id }}"', content)
        self.assertIn("Buscar responsable", content)
        self.assertIn("data-task-responsible-select", content)
        self.assertIn("item.request_type != 'COTIZACION'", content)

    def test_published_task_uses_fresh_read_with_local_fallback(self):
        content = Path("cotizacion_colectivos/services/task_publisher.py").read_text(encoding="utf-8")
        self.assertIn('module="Tasks"', content)
        self.assertIn("read_published_task", content)

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
