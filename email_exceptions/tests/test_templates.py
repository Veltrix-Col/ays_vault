from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.test import TestCase, override_settings
from django.urls import reverse

from email_exceptions.models import CaseActivity, CaseMessage, EmailAuditEvent, EmailException, EmailExceptionMessage, ExceptionCase, InboundEmail


class EmailExceptionsTemplateTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="email-viewer")
        self.user.user_permissions.add(
            Permission.objects.get(content_type__app_label="email_exceptions", codename="view_email_exceptions_operational")
        )
        self.client.force_login(self.user)
        email = InboundEmail.objects.create(source_mailbox="comunicaciones@segurosays.com", external_message_id="template-test-1", received_at="2026-09-12T10:32:00Z", from_name="Remitente sintético", from_email="pagos@bemsa.com.co", subject="Comprobante de pago BEMSA", body_text="Contenido sintético.")
        self.case = ExceptionCase.objects.create(case_key="case-template-test", organization="BEMSA", family="CARTERA_ARRENDAMIENTO", event_type="COMPROBANTE_PAGO", opened_at=email.received_at, last_activity_at=email.received_at)
        self.exception = EmailException.objects.create(primary_email=email, case=self.case, last_subject=email.subject, organization="BEMSA", family="CARTERA_ARRENDAMIENTO", event_type="COMPROBANTE_PAGO", exception_reason="Se recibió un comprobante de pago que requiere revisión.", rule_id="BEMSA_COMPROBANTE_PAGO_V1")
        EmailExceptionMessage.objects.create(exception=self.exception, email=email)
        CaseMessage.objects.create(case=self.case, email=email, role=CaseMessage.Role.OPENING, correlation_method=CaseMessage.CorrelationMethod.NEW_CASE, correlation_confidence=CaseMessage.CorrelationConfidence.LOW, correlation_reason="Apertura del caso")

    def test_list_uses_operational_banco_layout_and_filters(self):
        with CaptureQueriesContext(connection) as captured:
            response = self.client.get(reverse("email_exceptions:list"), {"q": "BEMSA", "status": "PENDING", "organization": "BEMSA"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "/static/css/email-exceptions.css")
        self.assertContains(response, 'class="email-exceptions-page"')
        self.assertContains(response, "Banco de Herramientas")
        self.assertContains(response, "Operaciones")
        self.assertContains(response, f'href="{reverse("public_home")}"')
        self.assertContains(response, f'href="{reverse("area_home", args=["operaciones"])}"')
        self.assertNotContains(response, "CardManager")
        self.assertContains(response, "Encontrar un caso")
        self.assertNotContains(response, "Vistas rápidas de casos")
        self.assertNotContains(response, "Estado de los casos")
        self.assertNotContains(response, "Agenda de atención")
        self.assertNotContains(response, "Casos por responsable")
        self.assertNotContains(response, "Tiempo de resolución")
        self.assertContains(response, "email-exception-table")
        self.assertContains(response, "Bandeja operativa por caso")
        self.assertContains(response, reverse("email_exceptions:case_detail", args=[self.case.pk]))
        self.assertContains(response, "Aplicar filtros")
        self.assertTrue(captured)
        self.assertFalse(any("body_text" in query["sql"].lower() for query in captured))

    def test_detail_uses_module_stylesheet_and_operational_ui(self):
        response = self.client.get(reverse("email_exceptions:detail", args=[self.exception.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "/static/css/email-exceptions.css")
        self.assertContains(response, 'class="email-exceptions-page"')
        self.assertContains(response, "Banco de Herramientas")
        self.assertContains(response, "Operaciones")
        self.assertContains(response, f'href="{reverse("area_home", args=["operaciones"])}"')
        self.assertContains(response, "Correo recibido")
        self.assertContains(response, "Clasificación")

    @override_settings(EMAIL_EXCEPTIONS_ZOHO_TASK_WRITE_ENABLED=True)
    def test_detail_uses_readable_timeline_and_collapsed_actions(self):
        response = self.client.get(reverse("email_exceptions:detail", args=[self.exception.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "/static/css/email-exceptions.css")
        self.assertContains(response, "Correo recibido")
        self.assertContains(response, "Crear tarea en Zoho")
        self.assertContains(response, "<details class=\"email-action-disclosure\">", html=False)
        self.assertContains(response, "Información técnica")
        self.assertNotContains(response, "EMAIL_RECEIVED")

    def test_detail_keeps_wide_main_content_and_realistic_side_column(self):
        response = self.client.get(reverse("email_exceptions:detail", args=[self.exception.pk]))
        stylesheet = (settings.BASE_DIR / "static" / "css" / "email-exceptions.css").read_text(encoding="utf-8")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "email-exception-detail-grid")
        self.assertContains(response, "email-detail-main")
        self.assertContains(response, "email-detail-aside")
        self.assertNotContains(response, "span-8 email-detail-main")
        self.assertIn("minmax(320px, 380px)", stylesheet)
        self.assertIn(".email-exceptions-page .email-action-card {", stylesheet)
        self.assertIn("grid-row: 1", stylesheet)
        self.assertIn(".email-exceptions-page .email-classification-card {", stylesheet)
        self.assertIn("grid-row: 2", stylesheet)
        self.assertIn("grid-column: 1 / -1", stylesheet)

    def test_html_body_is_rendered_as_escaped_readable_text(self):
        email = InboundEmail.objects.create(
            source_mailbox="comunicaciones@segurosays.com",
            external_message_id="html-message-1",
            conversation_id="conversation-1",
            received_at="2026-09-13T15:11:00Z",
            from_name="Contacto sintético",
            from_email="contacto@example.test",
            to=["comunicaciones@segurosays.com"],
            subject="Mensaje HTML",
            body_text="<html><head><style>.x{display:none}</style><script>alert('x')</script></head><body><p>Buenos días,</p><div>La reunión quedó programada.</div><a href=\"https://example.test/agenda\">Ver agenda</a></body></html>",
        )
        item = EmailException.objects.create(
            primary_email=email,
            last_subject=email.subject,
            exception_reason="Motivo sintético",
            rule_id="TEST_RULE",
        )
        response = self.client.get(reverse("email_exceptions:detail", args=[item.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Buenos días,")
        self.assertContains(response, "La reunión quedó programada.")
        self.assertContains(response, "Ver agenda (https://example.test/agenda)")
        self.assertNotContains(response, "<style>")
        self.assertNotContains(response, "alert('x')")
        self.assertContains(response, "html-message-1")
        self.assertContains(response, "Información técnica")
        self.assertContains(response, "Correo recibido")
        self.assertContains(response, "Clasificación")
        self.assertContains(response, "Lectura operativa")
        self.assertContains(response, "Regla")
        self.assertContains(response, "Motivo")

    def test_non_email_page_does_not_load_email_exceptions_stylesheet(self):
        response = self.client.get(reverse("public_home"))
        self.assertNotContains(response, "email-exceptions.css")

    def test_module_styles_are_scoped_to_a_light_operational_surface(self):
        stylesheet = (settings.BASE_DIR / "static" / "css" / "email-exceptions.css").read_text(encoding="utf-8")
        self.assertIn(".email-exceptions-page", stylesheet)
        self.assertIn("background: #fff", stylesheet)
        self.assertNotRegex(stylesheet, r"(?m)^body\s*\{")
        self.assertNotIn(":root", stylesheet)
        self.assertNotIn("linear-gradient", stylesheet)

    def test_case_detail_renders_case_timeline_and_linked_exception_once(self):
        response = self.client.get(reverse("email_exceptions:case_detail", args=[self.case.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Historia del caso")
        self.assertContains(response, "Apertura")
        self.assertContains(response, "Caso nuevo")
        self.assertContains(response, "Apertura del caso")
        self.assertContains(response, "Excepciones asociadas")
        self.assertContains(response, reverse("email_exceptions:detail", args=[self.exception.pk]))
        self.assertContains(response, "Contenido sintético.")

    def test_case_timeline_displays_roles_correlation_and_consolidated_exception_events(self):
        for index, role, method, confidence, reason in (
            (2, CaseMessage.Role.FOLLOW_UP, CaseMessage.CorrelationMethod.FUNCTIONAL_ID, CaseMessage.CorrelationConfidence.HIGH, "Identificador funcional coincidente"),
            (3, CaseMessage.Role.RESOLUTION, CaseMessage.CorrelationMethod.CONVERSATION, CaseMessage.CorrelationConfidence.HIGH, "Conversación coincidente"),
            (4, CaseMessage.Role.CONTEXT, CaseMessage.CorrelationMethod.STRUCTURED, CaseMessage.CorrelationConfidence.MEDIUM, "Contexto descriptivo"),
        ):
            email = InboundEmail.objects.create(
                source_mailbox="comunicaciones@segurosays.com",
                external_message_id=f"template-case-message-{index}",
                received_at=f"2026-09-12T10:{index:02d}:00Z",
                subject=f"Evidencia {role}",
                body_text=f"Cuerpo {role}.",
            )
            CaseMessage.objects.create(
                case=self.case, email=email, role=role, correlation_method=method,
                correlation_confidence=confidence, correlation_reason=reason,
            )
        EmailAuditEvent.objects.create(exception=self.exception, event_type="IGNORED", actor=self.user)

        response = self.client.get(reverse("email_exceptions:case_detail", args=[self.case.pk]))

        self.assertEqual(response.status_code, 200)
        for label in ("Apertura", "Seguimiento", "Resolución", "Contexto", "Alta · HIGH", "Media", "Identificador funcional coincidente", "Conversación coincidente", "Contexto descriptivo"):
            self.assertContains(response, label)
        self.assertContains(response, "Evento de excepción")
        self.assertContains(response, "Excepción ignorada")
        self.assertEqual(response.content.count(b"Comprobante de pago BEMSA"), 1)
        self.assertContains(response, "Cuerpo FOLLOW_UP.")

    def test_case_detail_warns_on_conflict_and_ambiguity_without_recomputing(self):
        for index, reason in enumerate(("conflicting_functional_identifiers", "ambiguous_multiple_candidates"), start=5):
            email = InboundEmail.objects.create(
                source_mailbox="comunicaciones@segurosays.com",
                external_message_id=f"template-case-warning-{index}",
                received_at="2026-09-12T11:00:00Z",
                subject=f"Evidencia {index}",
            )
            CaseMessage.objects.create(
                case=self.case, email=email, role=CaseMessage.Role.FOLLOW_UP,
                correlation_method=CaseMessage.CorrelationMethod.NEW_CASE,
                correlation_confidence=CaseMessage.CorrelationConfidence.LOW,
                correlation_reason=reason,
            )
        response = self.client.get(reverse("email_exceptions:case_detail", args=[self.case.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.count(b"no debe interpretarse como una fusi"), 2)

    def test_case_detail_consolidates_operational_activity_without_raw_metadata(self):
        CaseActivity.objects.create(
            case=self.case,
            event_type=CaseActivity.EventType.STATUS_CHANGED,
            actor=self.user,
            from_status=ExceptionCase.Status.OPEN,
            to_status=ExceptionCase.Status.IN_PROGRESS,
            metadata={"reason": "Revisión iniciada", "comment": "Tomado por analista"},
        )
        CaseActivity.objects.create(
            case=self.case,
            event_type=CaseActivity.EventType.NOTE_ADDED,
            actor=self.user,
            metadata={"note": "Solicitar soporte al remitente"},
        )
        response = self.client.get(reverse("email_exceptions:case_detail", args=[self.case.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Cronología consolidada")
        self.assertContains(response, "Actividad")
        self.assertContains(response, "Estado: Abierto → En gestión.")
        self.assertContains(response, "Nota interna.")
        self.assertContains(response, "Solicitar soporte al remitente")
        self.assertNotContains(response, '"from_user_id"')
        self.assertNotContains(response, '"reason"')

    def test_case_list_has_one_row_for_case_and_paginates(self):
        for index in range(205):
            email = InboundEmail.objects.create(source_mailbox="comunicaciones@segurosays.com", external_message_id=f"template-page-{index}", received_at="2026-09-12T10:32:00Z", subject=f"Caso adicional {index}")
            case = ExceptionCase.objects.create(case_key=f"case-page-{index}", organization="BEMSA", opened_at=email.received_at, last_activity_at=email.received_at)
            CaseMessage.objects.create(case=case, email=email, role=CaseMessage.Role.OPENING, correlation_method=CaseMessage.CorrelationMethod.NEW_CASE, correlation_confidence=CaseMessage.CorrelationConfidence.LOW)
        response = self.client.get(reverse("email_exceptions:list"), {"organization": "BEMSA"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["page_obj"].paginator.count, 206)
        self.assertEqual(len(response.context["cases"]), 50)
        response = self.client.get(reverse("email_exceptions:list"), {"page": 2, "organization": "BEMSA"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["cases"]), 50)
        self.assertContains(response, "organization=BEMSA&amp;page=1")
