from datetime import date
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from cotizacion_colectivos.models import (
    BillingExceptionRefreshRun, BillingOperationalCase, BillingTechnicalException,
)


@override_settings(COLECTIVOS_INTERNAL_PUBLIC_ACCESS=True)
class BillingExceptionViewTests(TestCase):
    def setUp(self):
        now = timezone.now()
        self.run = BillingExceptionRefreshRun.objects.create(
            profile="production", as_of=date(2026, 9, 7), status="SUCCESS",
            started_at=now, finished_at=now,
        )
        self.case = BillingOperationalCase.objects.create(
            case_key="billing-case/v1/missing-operation/id:1/installment:2@2026-09-01",
            kind="MISSING_OPERATION", policy_id="1", policy_number="POL-SEARCH",
            client_name="Cliente Buscable", insurer="Aseguradora", branch="Vida",
            installment_number=2, relevant_date=date(2026, 9, 1), local_priority=1,
            first_detected_at=now, last_detected_at=now, last_run=self.run,
        )
        self.finding = BillingTechnicalException.objects.create(
            exception_key="billing-exception/v1/a", source="revision_facturacion",
            exception_type="MISSING_OPERATION", rule_code="rule", reason="No existe operación",
            source_reference="source", policy_number="POL-SEARCH", installment_number=2,
            relevant_date=date(2026, 9, 1), first_detected_at=now, last_detected_at=now,
            last_run=self.run,
        )
        self.case.exceptions.add(self.finding)

    def add_case(self, suffix, *, priority=3, task_id="", branch="Vida", findings=()):
        now = timezone.now()
        case = BillingOperationalCase.objects.create(
            case_key=f"billing-case/v1/operation/id:{suffix}/operation:{suffix}",
            kind="OPERATION", policy_id=suffix, policy_number=f"POL-{suffix}",
            client_name=f"Cliente {suffix}", branch=branch, operation_id=suffix,
            operation_name=f"Cobro {suffix}", local_priority=priority, zoho_task_id=task_id,
            first_detected_at=now, last_detected_at=now, last_run=self.run,
        )
        for index, kind in enumerate(findings):
            finding = BillingTechnicalException.objects.create(
                exception_key=f"billing-exception/v1/{suffix}/{index}", source="revision_facturacion",
                exception_type=kind, rule_code="rule", reason="reason", source_reference="source",
                policy_number=f"POL-{suffix}", operation_id=suffix,
                first_detected_at=now, last_detected_at=now, last_run=self.run,
            )
            case.exceptions.add(finding)
        return case

    def test_five_visible_cards_keep_all_snapshot_metrics_and_filters(self):
        self.add_case("charge", priority=1, task_id="123456789012", findings=("MISSING_CHARGE",))
        self.add_case("multi", priority=2, branch="Salud", findings=(
            "MISSING_CERTIFICATE", "MISSING_EXPEDITION_DATE",
        ))
        url = reverse("cotizacion_colectivos:billing_exception_list")
        response = self.client.get(url)
        self.assertEqual(response.context["metrics"], {
            "active": 3, "high": 2, "without_task": 2, "with_task": 1,
            "missing_operation": 1, "missing_charge": 1, "multiple": 1,
        })
        for label in (
            "Casos activos", "Prioridad alta", "Operación faltante",
            "Cobro faltante", "Múltiples hallazgos",
        ):
            self.assertContains(response, f"<span>{label}</span>", html=True)
        self.assertNotContains(response, "<span>Sin tarea</span>", html=True)
        self.assertNotContains(response, "<span>Con tarea</span>", html=True)
        for quick, expected in (
            ("high", 2), ("without_task", 2), ("with_task", 1),
            ("missing_operation", 1), ("missing_charge", 1), ("multiple", 1),
        ):
            with self.subTest(card=quick):
                filtered = self.client.get(url, {"quick": quick})
                self.assertEqual(filtered.context["page"].paginator.count, expected)
                self.assertEqual(filtered.context["active_card"], quick)
        active = self.client.get(url, {"detection": "DETECTED"})
        self.assertEqual(active.context["page"].paginator.count, 3)
        self.assertEqual(active.context["active_card"], "active")

    def test_card_combines_with_existing_filter(self):
        self.add_case("multi", priority=2, branch="Salud", findings=(
            "MISSING_CERTIFICATE", "MISSING_EXPEDITION_DATE",
        ))
        response = self.client.get(reverse("cotizacion_colectivos:billing_exception_list"), {
            "quick": "multiple", "branch": "Salud", "query": "Cliente multi",
        })
        self.assertEqual(response.context["page"].paginator.count, 1)
        self.assertContains(response, "POL-multi")
        self.assertEqual(response.context["form"].cleaned_data["quick"], "multiple")

    def test_pagination_preserves_card_filter_and_returns_to_page_one_on_card_click(self):
        for index in range(25):
            self.add_case(f"high-{index:02}", priority=1)
        url = reverse("cotizacion_colectivos:billing_exception_list")
        response = self.client.get(url, {"quick": "high", "page": "2"})
        self.assertEqual(response.context["page"].number, 2)
        self.assertEqual(response.context["page"].paginator.count, 26)
        self.assertContains(response, "quick=high")
        self.assertNotIn("page=2", response.context["quick_card_urls"]["with_task"])

    def test_card_counts_stay_on_latest_success_while_new_run_is_pending(self):
        BillingExceptionRefreshRun.objects.create(
            profile="production", as_of=date(2026, 9, 8), status="PENDING",
        )
        response = self.client.get(reverse("cotizacion_colectivos:billing_exception_list"))
        self.assertEqual(response.context["metrics"]["active"], 1)
        self.assertEqual(response.context["metrics"]["missing_operation"], 1)
        self.assertEqual(response.context["page"].paginator.count, 1)

    def test_list_search_filters_and_actions_column(self):
        response = self.client.get(reverse("cotizacion_colectivos:billing_exception_list"), {
            "query": "Buscable", "priority": "1", "finding": "MISSING_OPERATION",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "POL-SEARCH")
        self.assertContains(response, "Acciones")
        self.assertContains(response, "Operación no encontrada")

    def test_list_hides_task_columns_and_actions_but_keeps_case_actions(self):
        self.finding.context = {
            "policy_link": "https://crm.zoho.com/crm/org/module/Polizas/1",
            "operation_link": "https://crm.zoho.com/crm/org/module/Opeeraciones/2",
        }
        self.finding.save(update_fields=("context",))
        response = self.client.get(reverse("cotizacion_colectivos:billing_exception_list"))
        self.assertNotContains(response, "<th>Responsable</th>", html=True)
        self.assertNotContains(response, "<th>Tarea Zoho</th>", html=True)
        self.assertNotContains(response, "Crear tarea protegida")
        self.assertContains(response, "Ver detalle")
        self.assertContains(response, "Ver póliza Zoho")

    def test_detail_moves_note_and_observations_out_of_technical_information(self):
        self.finding.context = {
            "note": "Seguimiento desde la nota de Zoho",
            "observations": "Observación registrada en la operación",
            "diagnostics": "MISSING_OPERATION",
            "policy_link": "https://crm.zoho.com/crm/org/module/Polizas/1",
        }
        self.finding.save(update_fields=("context",))
        response = self.client.get(reverse(
            "cotizacion_colectivos:billing_exception_detail", args=(self.case.pk,),
        ))
        self.assertContains(response, "4 · Observaciones / seguimiento")
        self.assertContains(response, "Observaciones de la operación")
        self.assertContains(response, "Nota de seguimiento asociada")
        self.assertContains(response, "Seguimiento desde la nota de Zoho", count=1)
        self.assertContains(response, "Observación registrada en la operación", count=1)
        self.assertNotContains(response, "<strong>note:</strong>", html=True)
        self.assertNotContains(response, "<strong>observations:</strong>", html=True)
        self.assertContains(response, "Diagnósticos:")
        self.assertContains(response, "Enlace de la póliza:")
        self.assertContains(response, "Clave del caso:")
        self.assertContains(response, "Clave de la excepción:")
        self.assertContains(response, "5 · Historial de detección")
        self.assertContains(response, "6 · Información técnica")

    def test_followup_section_is_conditional_for_each_available_source(self):
        url = reverse("cotizacion_colectivos:billing_exception_detail", args=(self.case.pk,))
        response = self.client.get(url)
        self.assertNotContains(response, "Observaciones y seguimiento")
        self.assertContains(response, "4 · Historial de detección")
        self.assertContains(response, "5 · Información técnica")
        for context, visible, hidden in (
            ({"note": "Solo nota"}, "Nota de seguimiento asociada", "Observaciones de la operación"),
            ({"observations": "Solo observación"}, "Observaciones de la operación", "Nota de seguimiento asociada"),
        ):
            with self.subTest(context=context):
                self.finding.context = context
                self.finding.save(update_fields=("context",))
                response = self.client.get(url)
                self.assertContains(response, "Observaciones y seguimiento")
                self.assertContains(response, visible)
                self.assertNotContains(response, hidden)
                self.assertContains(response, "5 · Historial de detección")
                self.assertContains(response, "6 · Información técnica")

    def test_detail_hides_task_section_and_uses_spanish_visible_labels(self):
        self.case.zoho_task_id = "123456789012"
        self.case.zoho_task_status = "Completed"
        self.case.zoho_task_responsible = "Responsable oculto"
        self.case.save(update_fields=("zoho_task_id", "zoho_task_status", "zoho_task_responsible"))
        response = self.client.get(reverse(
            "cotizacion_colectivos:billing_exception_detail", args=(self.case.pk,),
        ))
        self.assertNotContains(response, "4 · Gestión Zoho")
        self.assertNotContains(response, "Tarea Zoho")
        self.assertNotContains(response, "Responsable oculto")
        self.assertNotContains(response, "case_key:")
        self.assertNotContains(response, "exception_key:")
        self.assertNotContains(response, "source_reference")
        self.assertContains(response, "4 · Historial de detección")
        self.assertContains(response, "5 · Información técnica")

    def test_visible_copy_does_not_use_critical_english_labels(self):
        list_response = self.client.get(reverse("cotizacion_colectivos:billing_exception_list"))
        self.assertNotContains(list_response, "Snapshot local")
        self.assertNotContains(list_response, ">Task<")
        detail_response = self.client.get(reverse(
            "cotizacion_colectivos:billing_exception_detail", args=(self.case.pk,),
        ))
        for label in ("case_key:", "exception_key:", "Diagnostics:", "Note:", "Observations:"):
            with self.subTest(label=label):
                self.assertNotContains(detail_response, label)

    @patch("cotizacion_colectivos.billing_exception_views.queue_billing_exceptions_refresh")
    def test_read_only_pages_do_not_queue_or_write(self, queue_refresh):
        self.client.get(reverse("cotizacion_colectivos:billing_exception_list"))
        self.client.get(reverse(
            "cotizacion_colectivos:billing_exception_detail", args=(self.case.pk,),
        ))
        queue_refresh.assert_not_called()

    def test_valid_snapshot_remains_visible_during_pending_run(self):
        BillingExceptionRefreshRun.objects.create(
            profile="production", as_of=date(2026, 9, 8), status="PENDING",
        )
        response = self.client.get(reverse("cotizacion_colectivos:billing_exception_list"))
        self.assertContains(response, "POL-SEARCH")
        self.assertContains(response, "Puede continuar trabajando con la última información válida")
        self.assertEqual(response.context["metrics"]["active"], 1)
        self.assertEqual(response.context["page"].paginator.count, 1)

    def test_valid_snapshot_remains_visible_during_running_run(self):
        BillingExceptionRefreshRun.objects.create(
            profile="production", as_of=date(2026, 9, 8), status="RUNNING",
            started_at=timezone.now(),
        )
        response = self.client.get(reverse("cotizacion_colectivos:billing_exception_list"))
        self.assertContains(response, "POL-SEARCH")
        self.assertEqual(response.context["latest_success"], self.run)

    def test_failed_run_preserves_same_snapshot_for_metrics_and_table(self):
        BillingExceptionRefreshRun.objects.create(
            profile="production", as_of=date(2026, 9, 8), status="FAILED", safe_error="safe",
        )
        response = self.client.get(reverse("cotizacion_colectivos:billing_exception_list"))
        self.assertContains(response, "POL-SEARCH")
        self.assertContains(response, "Se conserva la última información válida")
        self.assertEqual(response.context["metrics"]["active"], response.context["page"].paginator.count)

    def test_new_successful_snapshot_replaces_previous_snapshot(self):
        newer = BillingExceptionRefreshRun.objects.create(
            profile="production", as_of=date(2026, 9, 8), status="SUCCESS",
            started_at=timezone.now(), finished_at=timezone.now(),
        )
        self.case.detection_status = "NOT_DETECTED"
        self.case.last_run = newer
        self.case.save(update_fields=("detection_status", "last_run"))
        BillingOperationalCase.objects.create(
            case_key="billing-case/v1/policy-gap/id:2/policy-gap", kind="POLICY_GAP",
            policy_id="2", policy_number="POL-NEW", local_priority=1,
            first_detected_at=timezone.now(), last_detected_at=timezone.now(), last_run=newer,
        )
        response = self.client.get(reverse("cotizacion_colectivos:billing_exception_list"))
        self.assertContains(response, "POL-NEW")
        self.assertNotContains(response, "POL-SEARCH")
        self.assertEqual(response.context["metrics"]["active"], 1)
        self.assertEqual(response.context["page"].paginator.count, 1)

    def test_detail_has_functional_finding_and_no_operation_link(self):
        response = self.client.get(reverse("cotizacion_colectivos:billing_exception_detail", args=(self.case.pk,)))
        self.assertContains(response, "Operación faltante")
        self.assertContains(response, "Operación no encontrada")
        self.assertNotContains(response, "Ver operación en Zoho")

    def test_empty_state_and_failed_refresh_state(self):
        BillingOperationalCase.objects.all().delete()
        response = self.client.get(reverse("cotizacion_colectivos:billing_exception_list"))
        self.assertContains(response, "No se detectaron excepciones")
        BillingExceptionRefreshRun.objects.create(
            profile="production", as_of=date(2026, 9, 8), status="FAILED", safe_error="safe",
        )
        response = self.client.get(reverse("cotizacion_colectivos:billing_exception_list"))
        self.assertContains(response, "Se conserva la última información válida", html=False)

    @patch("cotizacion_colectivos.billing_exception_views.timezone.localdate")
    @patch("cotizacion_colectivos.billing_exception_views.queue_billing_exceptions_refresh")
    def test_refresh_post_uses_current_local_date(self, queue_refresh, localdate):
        localdate.return_value = date(2026, 9, 8)
        url = reverse("cotizacion_colectivos:billing_exception_refresh")
        self.assertEqual(self.client.get(url).status_code, 405)
        response = self.client.post(url, {"as_of": "bad"})
        self.assertEqual(response.status_code, 302)
        queue_refresh.assert_called_once_with(as_of=date(2026, 9, 8), requested_by=None)

    def test_list_hides_cutoff_input_task_cards_and_as_of_copy(self):
        response = self.client.get(reverse("cotizacion_colectivos:billing_exception_list"))
        self.assertNotContains(response, "Fecha de corte")
        self.assertNotContains(response, 'name="as_of"')
        self.assertContains(response, "Última actualización:")
        self.assertNotContains(response, "Última actualización válida")
        self.assertNotContains(response, " · corte ")
        self.assertNotContains(response, "<span>Sin tarea</span>", html=True)
        self.assertNotContains(response, "<span>Con tarea</span>", html=True)
