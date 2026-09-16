from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.test import TestCase
from django.utils import timezone

from email_exceptions.models import CaseMessage, EmailException, ExceptionCase, InboundEmail


class ExceptionCaseModelTests(TestCase):
    def setUp(self):
        now = timezone.now()
        self.case = ExceptionCase.objects.create(
            case_key="new-case-1",
            status=ExceptionCase.Status.OPEN,
            opened_at=now,
            last_activity_at=now,
        )
        self.email = InboundEmail.objects.create(
            source_mailbox="comunicaciones@segurosays.com",
            external_message_id="case-model-email-1",
            received_at=now,
            subject="Falla sintética",
            body_text="Contenido sintético",
        )

    def test_case_supports_open_pending_and_resolved_with_timestamp(self):
        self.assertEqual(self.case.status, ExceptionCase.Status.OPEN)
        pending = ExceptionCase.objects.create(case_key="new-case-2", status=ExceptionCase.Status.PENDING, opened_at=timezone.now(), last_activity_at=timezone.now())
        resolved = ExceptionCase.objects.create(case_key="new-case-3", status=ExceptionCase.Status.RESOLVED, opened_at=timezone.now(), last_activity_at=timezone.now(), resolved_at=timezone.now())
        self.assertEqual(pending.status, "PENDING")
        self.assertEqual(resolved.status, "RESOLVED")
        self.assertIsNotNone(resolved.resolved_at)

    def test_case_key_is_unique(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ExceptionCase.objects.create(case_key=self.case.case_key, opened_at=timezone.now(), last_activity_at=timezone.now())

    def test_case_message_roles_and_correlations(self):
        opening = CaseMessage.objects.create(case=self.case, email=self.email, role=CaseMessage.Role.OPENING, correlation_method=CaseMessage.CorrelationMethod.NEW_CASE, correlation_confidence=CaseMessage.CorrelationConfidence.HIGH)
        resolution = CaseMessage.objects.create(case=self.case, email=InboundEmail.objects.create(source_mailbox=self.email.source_mailbox, external_message_id="case-model-email-2", received_at=timezone.now()), role=CaseMessage.Role.RESOLUTION, correlation_method=CaseMessage.CorrelationMethod.FUNCTIONAL_ID, correlation_confidence=CaseMessage.CorrelationConfidence.MEDIUM)
        self.assertEqual(opening.role, "OPENING")
        self.assertEqual(resolution.role, "RESOLUTION")
        self.assertIsNone(opening.linked_by)

    def test_duplicate_case_email_is_rejected_but_email_can_be_linked_to_another_case(self):
        CaseMessage.objects.create(case=self.case, email=self.email, role=CaseMessage.Role.OPENING, correlation_method=CaseMessage.CorrelationMethod.NEW_CASE, correlation_confidence=CaseMessage.CorrelationConfidence.HIGH)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                CaseMessage.objects.create(case=self.case, email=self.email, role=CaseMessage.Role.CONTEXT, correlation_method=CaseMessage.CorrelationMethod.MANUAL, correlation_confidence=CaseMessage.CorrelationConfidence.LOW)
        other = ExceptionCase.objects.create(case_key="new-case-4", opened_at=timezone.now(), last_activity_at=timezone.now())
        linked = CaseMessage.objects.create(case=other, email=self.email, role=CaseMessage.Role.CONTEXT, correlation_method=CaseMessage.CorrelationMethod.MANUAL, correlation_confidence=CaseMessage.CorrelationConfidence.LOW)
        self.assertEqual(linked.email_id, self.email.pk)

    def test_email_exception_case_is_optional_and_protected_on_delete(self):
        exception = EmailException.objects.create(primary_email=self.email, last_subject=self.email.subject, case=self.case)
        self.assertEqual(exception.case_id, self.case.pk)
        with self.assertRaises(ProtectedError):
            self.case.delete()
        exception.case = None
        exception.save(update_fields=("case",))
        self.case.delete()
        self.assertFalse(ExceptionCase.objects.filter(pk=self.case.pk).exists())

    def test_case_models_do_not_duplicate_body_or_payload(self):
        case_fields = {field.name for field in ExceptionCase._meta.fields}
        message_fields = {field.name for field in CaseMessage._meta.fields}
        self.assertNotIn("body_text", case_fields | message_fields)
        self.assertNotIn("payload", case_fields | message_fields)

    def test_choices_are_exactly_the_initial_catalogs(self):
        self.assertEqual(tuple(ExceptionCase.Status.values), ("OPEN", "PENDING", "RESOLVED"))
        self.assertEqual(tuple(CaseMessage.Role.values), ("OPENING", "FOLLOW_UP", "RESOLUTION", "CONTEXT"))
