from pathlib import Path

from django.test import SimpleTestCase
from django.urls import reverse

from cotizacion_colectivos.adjustments import allowed_adjustments
from cotizacion_colectivos.services.task_publisher import (
    ColectivosTaskPayload,
    TaskPublishingDisabled,
    get_task_publisher,
)


class ProductToolsContractTests(SimpleTestCase):
    def test_current_novelties_are_only_entry_and_retirement(self):
        codes = {item.code for item in allowed_adjustments("91")}
        self.assertEqual(codes, {"SIN_CAMBIOS", "INCLUSION", "RETIRO"})

    def test_three_canonical_tool_routes_exist(self):
        self.assertEqual(reverse("cotizacion_colectivos:novelties_index"), "/cotizacion-colectivos/novedades/")
        self.assertEqual(reverse("cotizacion_colectivos:individual_index"), "/cotizacion-colectivos/cotizacion-individual/")
        self.assertEqual(reverse("cotizacion_colectivos:invitations_index"), "/cotizacion-colectivos/invitaciones-aseguradoras/")

    def test_active_templates_do_not_expose_renewals_or_effective_date(self):
        templates = (
            Path("templates/cotizacion_colectivos/index.html"),
            Path("templates/cotizacion_colectivos/policy_detail.html"),
            Path("templates/cotizacion_colectivos/external/portal.html"),
            Path("templates/cotizacion_colectivos/external/_functional_entity.html"),
        )
        content = "\n".join(path.read_text(encoding="utf-8") for path in templates)
        self.assertNotIn("Solicitudes y Renovaciones", content)
        self.assertNotIn(">Renovación<", content)
        self.assertNotIn("Fecha efectiva", content)
        self.assertIn("Fecha solicitada de ingreso", content)
        self.assertIn("Fecha solicitada de retiro", content)

    def test_task_publisher_remains_disabled_by_default(self):
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
