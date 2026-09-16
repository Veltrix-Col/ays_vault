from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from email_exceptions.models import EmailException, InboundEmail
from email_exceptions.services import classify_inbound_email, ingest_payload


@override_settings(
    EMAIL_EXCEPTIONS_ENABLED=True,
    TOOLS_ACCESS_MODE="local_public",
    RUNNING_TESTS=True,
)
class InboundClassificationTests(TestCase):

    def _email(self, subject, body_text="", from_email="sender@example.test", source_mailbox="comunicaciones@segurosays.com"):
        return InboundEmail(
            source_mailbox=source_mailbox,
            subject=subject,
            body_text=body_text,
            from_name="Remitente sintético",
            from_email=from_email,
        )

    def test_strong_events_do_not_require_known_organization(self):
        result = classify_inbound_email(self._email("Doble pago correspondiente al periodo de agosto"))

        self.assertTrue(result.is_exception)
        self.assertEqual(result.message_outcome, "FAILURE")
        self.assertTrue(result.action_required)
        self.assertEqual(result.action_type, "RECONCILE_PAYMENT")
        self.assertEqual(result.scope, "CASE")
        self.assertEqual(result.event_type, "PAGO_DOBLE")
        self.assertEqual(result.organization, "")

    def test_refined_contextual_rules_require_specific_context(self):
        cases = (
            ("Pago duplicado póliza 123", "PAGO_DOBLE"),
            ("No hemos recibido las facturas correspondientes al mes de agosto", "SOLICITUD_FACTURA"),
            ("Se presenta inconsistencia en los aportes del empleador", "INCONSISTENCIA_PAGO_EMPLEADOR"),
            ("Documentación pendiente para completar el trámite", "DOCUMENTACION_FALTANTE"),
            ("Solicitud de emisión de póliza Ziklo", "SOLICITUD_EMISION"),
            ("Cobro: documentos pendientes", "COBRO_DOCUMENTOS"),
        )
        for subject, event_type in cases:
            with self.subTest(subject=subject):
                result = classify_inbound_email(self._email(subject))
                self.assertTrue(result.is_exception)
                self.assertEqual(result.event_type, event_type)

    def test_generic_terms_and_informative_messages_are_no_match(self):
        cases = (
            "Adjunto factura",
            "Adjunto documentos",
            "Adjunto relación de aportes del mes",
            "Reunión comercial martes a las 10",
            "Gracias, factura contabilizada",
            "Boletín informativo SURA",
            "Documentos adjuntos para su información",
            "Consulta sobre cobro",
        )
        for subject in cases:
            with self.subTest(subject=subject):
                result = classify_inbound_email(self._email(subject))
                self.assertFalse(result.is_exception)
                self.assertEqual(result.event_type, "")
                self.assertIn(result.message_outcome, {"INFORMATIONAL", "NOISE", "UNCLEAR"})
                self.assertEqual(result.action_type, "NONE")

    def test_sura_mailbox_requires_actionable_content(self):
        informative = classify_inbound_email(self._email(
            "Boletín informativo SURA",
            source_mailbox="aysltda@asesorsura.com",
        ))
        actionable = classify_inbound_email(self._email(
            "Inconsistencia en los aportes del empleador",
            source_mailbox="aysltda@asesorsura.com",
        ))

        self.assertFalse(informative.is_exception)
        self.assertTrue(actionable.is_exception)
        self.assertEqual(actionable.event_type, "INCONSISTENCIA_PAGO_EMPLEADOR")

    def test_forwarded_message_can_infer_organization_from_body(self):
        result = classify_inbound_email(self._email(
            "Comprobante de pago correspondiente a la póliza M-600000598",
            "De: pagosarrendamientos@bemsa.com.co\nAdjunto comprobante de pago.",
            from_email="claudia@segurosays.com",
        ))

        self.assertTrue(result.is_exception)
        self.assertEqual(result.organization, "BEMSA")
        self.assertEqual(result.event_type, "COMPROBANTE_PAGO")

    def test_comprobante_requires_context_but_accepts_insurer_context(self):
        without_context = classify_inbound_email(self._email("Comprobante de pago"))
        with_context = classify_inbound_email(self._email("Comprobante de pago remitido a la aseguradora"))

        self.assertFalse(without_context.is_exception)
        self.assertTrue(with_context.is_exception)
        self.assertEqual(with_context.event_type, "COMPROBANTE_PAGO")

    def _payload(self, *, message_id, subject, body_text, sender="pagosarrendamientos@bemsa.com.co"):
        return {
            "source_mailbox": "comunicaciones@segurosays.com",
            "message_id": message_id,
            "subject": subject,
            "body_text": body_text,
            "from": {"name": "Remitente sintético", "email": sender},
        }

    def test_conclusive_bemsa_rule_creates_exception_and_marks_email(self):
        email, exception, created = ingest_payload(self._payload(
            message_id="classification-bemsa-1",
            subject="Comprobante de pago Seguros Mundial - BEMSA S.A.S",
            body_text="Adjunto comprobante de pago de la póliza M-600000598.",
        ))

        self.assertTrue(created)
        self.assertEqual(email.classification_status, InboundEmail.CLASSIFICATION_EXCEPTION)
        self.assertEqual(email.classification_rule_id, "PAYMENT_RECEIPT_CONTEXTUAL_V631")
        self.assertTrue(email.processed_at)
        self.assertIsNotNone(exception)
        self.assertEqual(exception.status, EmailException.PENDING)

    def test_normal_mail_is_no_match_and_not_in_operational_inbox(self):
        email, exception, created = ingest_payload(self._payload(
            message_id="classification-normal-1",
            subject="Test - Reunión de seguimiento comercial",
            body_text="Les confirmo que la reunión de seguimiento comercial quedó programada.",
            sender="comercial@example.test",
        ))

        self.assertTrue(created)
        self.assertEqual(email.classification_status, InboundEmail.CLASSIFICATION_NO_MATCH)
        self.assertEqual(email.classification_rule_id, "")
        self.assertTrue(email.classification_reason.strip())
        self.assertTrue(email.processed_at)
        self.assertIsNone(exception)
        self.assertEqual(EmailException.objects.count(), 0)

    def test_success_does_not_become_exception_and_beats_quoted_failure(self):
        result = classify_inbound_email(self._email(
            "Operación procesada correctamente",
            "El inconveniente fue solucionado.\nLa operación fue procesada correctamente.\n\n-----Mensaje anterior-----\nError en emisión de póliza.",
        ))

        self.assertEqual(result.message_outcome, "SUCCESS")
        self.assertFalse(result.current_message_is_exception)
        self.assertFalse(result.action_required)
        self.assertEqual(result.action_type, "NONE")
        self.assertFalse(result.is_exception)

    def test_completed_signature_and_legalization_are_not_pending_failures(self):
        for subject in ("Firma firmada exitosamente", "Pago legalizado correctamente"):
            with self.subTest(subject=subject):
                result = classify_inbound_email(self._email(subject))
                self.assertEqual(result.message_outcome, "SUCCESS")
                self.assertFalse(result.action_required)
                self.assertEqual(result.action_type, "NONE")
                self.assertFalse(result.is_exception)

    def test_required_complement_is_one_general_actionable_capability(self):
        result = classify_inbound_email(self._email(
            "Gestión de complementos",
            "Se requiere documentación adicional para continuar el proceso.",
            from_email="unknown@example.test",
        ))

        self.assertEqual(result.rule_id, "COMPLEMENT_REQUIRED_V631")
        self.assertEqual(result.message_outcome, "PENDING_ACTION")
        self.assertTrue(result.action_required)
        self.assertEqual(result.action_type, "SEND_DOCUMENTS")
        self.assertTrue(result.eligible_for_email_exception)

    def test_informative_document_mention_and_isolated_new_appointment_are_not_actionable(self):
        for subject in ("Documentos adjuntos para su información", "Nueva cita confirmada"):
            with self.subTest(subject=subject):
                result = classify_inbound_email(self._email(subject))
                self.assertFalse(result.action_required)
                self.assertFalse(result.is_exception)
                self.assertEqual(result.action_type, "NONE")

    def test_universal_rules_do_not_require_an_organization(self):
        cases = (
            ("Solicitud de reembolso rechazada", "REEMBOLSO_RECHAZADO", "REVIEW"),
            ("Operación Póliza Nueva con Inconsistencias", "INCONSISTENCIA", "CORRECT_INFORMATION"),
            ("Se presenta inconsistencia en los aportes del empleador", "INCONSISTENCIA_PAGO_EMPLEADOR", "CORRECT_INFORMATION"),
            ("Pago duplicado correspondiente al periodo de agosto", "PAGO_DOBLE", "RECONCILE_PAYMENT"),
            ("El pago se encuentra pendiente de legalizacion", "PAGO_PENDIENTE_LEGALIZACION", "LEGALIZE_PAYMENT"),
            ("Firma remota rechazada", "FIRMA_RECHAZADA", "FOLLOW_UP"),
            ("Se requiere corregir la informacion para continuar con la emisión", "CORRECCION_INFORMACION", "CORRECT_INFORMATION"),
        )
        for subject, event, action_type in cases:
            with self.subTest(subject=subject):
                result = classify_inbound_email(self._email(subject, from_email="unknown@example.test"))
                self.assertTrue(result.is_exception)
                self.assertTrue(result.action_required)
                self.assertEqual(result.event_type, event)
                self.assertEqual(result.action_type, action_type)
                self.assertEqual(result.scope, "CASE")

    def test_batch_and_platform_are_not_individual_exceptions(self):
        batch = classify_inbound_email(self._email("Listado de 120 pólizas con inconsistencia PBS"))
        platform = classify_inbound_email(self._email("Contingencia plataforma: servicio de emisión no disponible"))

        self.assertEqual(batch.scope, "BATCH")
        self.assertEqual(batch.message_outcome, "FAILURE")
        self.assertTrue(batch.action_required)
        self.assertTrue(batch.is_exception)
        self.assertEqual(batch.action_type, "EXPAND_ATTACHMENT")
        self.assertFalse(batch.eligible_for_email_exception)
        self.assertEqual(platform.scope, "PLATFORM")
        self.assertEqual(platform.message_outcome, "FAILURE")
        self.assertTrue(platform.action_required)
        self.assertTrue(platform.is_exception)
        self.assertFalse(platform.eligible_for_email_exception)

    def test_action_type_catalog_includes_all_v631_values(self):
        from email_exceptions.services import ACTION_TYPES

        self.assertEqual(len(ACTION_TYPES), 10)
        self.assertIn("EXPAND_ATTACHMENT", ACTION_TYPES)

    def test_unclear_is_reachable_without_becoming_an_exception(self):
        result = classify_inbound_email(self._email("Consulta general", "Necesito confirmar el estado."))

        self.assertEqual(result.message_outcome, "UNCLEAR")
        self.assertFalse(result.action_required)
        self.assertFalse(result.is_exception)
        self.assertEqual(result.action_type, "NONE")

    def test_protected_negative_messages_are_not_exceptions(self):
        cases = (
            ("Adjunto factura correspondiente al mes.", "INFORMATIONAL"),
            ("Presentamos el recibo del seguro de Autos.", "INFORMATIONAL"),
            ("Tu seguro ha sido renovado.", "SUCCESS"),
            ("Pago aplicado correctamente.", "SUCCESS"),
            ("Firma remota completada exitosamente.", "SUCCESS"),
            ("Comprobante de pago", "UNCLEAR"),
            ("Boletín informativo SURA", "NOISE"),
            ("Notificación de actividad en ShareFile", "NOISE"),
            ("Reunión de seguimiento", "NOISE"),
        )
        for subject, outcome in cases:
            with self.subTest(subject=subject):
                result = classify_inbound_email(self._email(subject))
                self.assertEqual(result.message_outcome, outcome)
                self.assertFalse(result.action_required)
                self.assertFalse(result.is_exception)
        response = self.client.get(reverse("email_exceptions:list"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Test - Reunión de seguimiento comercial")

    def test_retry_of_normal_mail_is_idempotent_and_remains_no_match(self):
        payload = self._payload(
            message_id="classification-normal-retry",
            subject="Test - Reunión de seguimiento comercial",
            body_text="La reunión quedó programada.",
            sender="comercial@example.test",
        )
        first = ingest_payload(payload)
        second = ingest_payload(payload)

        self.assertTrue(first[2])
        self.assertFalse(second[2])
        self.assertEqual(InboundEmail.objects.count(), 1)
        self.assertEqual(EmailException.objects.count(), 0)
        self.assertEqual(second[0].classification_status, InboundEmail.CLASSIFICATION_NO_MATCH)

    def test_retry_of_exception_is_idempotent(self):
        payload = self._payload(
            message_id="classification-exception-retry",
            subject="Comprobante de pago BEMSA",
            body_text="Adjunto comprobante de pago de la póliza M-600000598.",
        )
        first = ingest_payload(payload)
        second = ingest_payload(payload)

        self.assertTrue(first[2])
        self.assertFalse(second[2])
        self.assertEqual(InboundEmail.objects.count(), 1)
        self.assertEqual(EmailException.objects.count(), 1)
        self.assertEqual(second[1].pk, first[1].pk)

    @patch("email_exceptions.services.classify_inbound_email", side_effect=RuntimeError("secret backend detail"))
    def test_classification_error_is_persisted_without_operational_exception(self, classify):
        email, exception, created = ingest_payload(self._payload(
            message_id="classification-error-1",
            subject="Correo para clasificar",
            body_text="Contenido sintético.",
            sender="comercial@example.test",
        ))

        self.assertTrue(created)
        self.assertTrue(classify.called)
        self.assertEqual(email.classification_status, InboundEmail.CLASSIFICATION_ERROR)
        self.assertEqual(email.classification_reason, "No fue posible completar la clasificación.")
        self.assertEqual(email.classification_rule_id, "")
        self.assertTrue(email.processed_at)
        self.assertIsNone(exception)
        self.assertEqual(EmailException.objects.count(), 0)
