from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client, TestCase
from django.utils import timezone

from email_exceptions.case_workflow import CaseTransitionError, transition_case
from email_exceptions.models import CaseActivity, CaseMessage, EmailException, ExceptionCase, InboundEmail


class ExceptionCaseWorkflowTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="case-operator", password="safe-password")
        permission = Permission.objects.get(
            content_type__app_label="email_exceptions",
            codename="operate_email_exceptions",
        )
        self.user.user_permissions.add(permission)

    def case(self, status=ExceptionCase.Status.OPEN):
        now = timezone.now()
        return ExceptionCase.objects.create(
            case_key=f"workflow-{ExceptionCase.objects.count()}-{status}",
            status=status,
            opened_at=now,
            last_activity_at=now,
        )

    def transition(self, case, target, **kwargs):
        return transition_case(case.pk, target, actor=self.user, **kwargs)

    def test_open_transitions_to_in_progress_and_audits(self):
        case = self.case()
        updated, activity = self.transition(case, ExceptionCase.Status.IN_PROGRESS)
        self.assertEqual(updated.status, ExceptionCase.Status.IN_PROGRESS)
        self.assertEqual((activity.from_status, activity.to_status), ("OPEN", "IN_PROGRESS"))
        self.assertEqual(activity.event_type, CaseActivity.EventType.STATUS_CHANGED)
        self.assertEqual(activity.actor_id, self.user.pk)

    def test_allowed_state_matrix(self):
        transitions = (
            (ExceptionCase.Status.OPEN, ExceptionCase.Status.WAITING),
            (ExceptionCase.Status.IN_PROGRESS, ExceptionCase.Status.WAITING),
            (ExceptionCase.Status.WAITING, ExceptionCase.Status.IN_PROGRESS),
            (ExceptionCase.Status.WAITING, ExceptionCase.Status.RESOLVED),
            (ExceptionCase.Status.RESOLVED, ExceptionCase.Status.CLOSED),
            (ExceptionCase.Status.RESOLVED, ExceptionCase.Status.IN_PROGRESS),
            (ExceptionCase.Status.CLOSED, ExceptionCase.Status.IN_PROGRESS),
            (ExceptionCase.Status.PENDING, ExceptionCase.Status.IN_PROGRESS),
        )
        for source, target in transitions:
            with self.subTest(source=source, target=target):
                case = self.case(source)
                kwargs = {"reason": "Revisión operativa"} if (
                    target == ExceptionCase.Status.RESOLVED
                    or (target == ExceptionCase.Status.IN_PROGRESS and source in {ExceptionCase.Status.RESOLVED, ExceptionCase.Status.CLOSED})
                ) else {}
                self.transition(case, target, **kwargs)
                self.assertEqual(ExceptionCase.objects.get(pk=case.pk).status, target)

    def test_in_progress_resolves_with_reason(self):
        case = self.case(ExceptionCase.Status.IN_PROGRESS)
        _, activity = self.transition(case, ExceptionCase.Status.RESOLVED, reason="Pago validado", comment="Confirmado con soporte")
        case.refresh_from_db()
        self.assertEqual(case.status, ExceptionCase.Status.RESOLVED)
        self.assertIsNotNone(case.resolved_at)
        self.assertIsNone(case.closed_at)
        self.assertEqual(activity.event_type, CaseActivity.EventType.RESOLVED)
        self.assertEqual(activity.metadata, {"reason": "Pago validado", "comment": "Confirmado con soporte"})

    def test_resolve_requires_reason(self):
        case = self.case(ExceptionCase.Status.IN_PROGRESS)
        with self.assertRaises(CaseTransitionError):
            self.transition(case, ExceptionCase.Status.RESOLVED)
        self.assertEqual(CaseActivity.objects.count(), 0)
        self.assertEqual(ExceptionCase.objects.get(pk=case.pk).status, ExceptionCase.Status.IN_PROGRESS)

    def test_resolved_closes_and_reopening_clears_current_resolution(self):
        case = self.case(ExceptionCase.Status.RESOLVED)
        case.resolved_at = timezone.now()
        case.save(update_fields=("resolved_at",))
        _, closed = self.transition(case, ExceptionCase.Status.CLOSED)
        case.refresh_from_db()
        self.assertIsNotNone(case.closed_at)
        self.assertEqual(closed.event_type, CaseActivity.EventType.CLOSED)
        _, reopened = self.transition(case, ExceptionCase.Status.IN_PROGRESS, reason="Nuevo soporte recibido")
        case.refresh_from_db()
        self.assertEqual(case.status, ExceptionCase.Status.IN_PROGRESS)
        self.assertIsNone(case.resolved_at)
        self.assertIsNone(case.closed_at)
        self.assertEqual(reopened.event_type, CaseActivity.EventType.REOPENED)

    def test_reopening_requires_reason(self):
        for status in (ExceptionCase.Status.RESOLVED, ExceptionCase.Status.CLOSED):
            with self.subTest(status=status):
                case = self.case(status)
                with self.assertRaises(CaseTransitionError):
                    self.transition(case, ExceptionCase.Status.IN_PROGRESS)
                self.assertEqual(CaseActivity.objects.count(), 0)

    def test_invalid_transitions_destination_pending_and_noop_are_rejected(self):
        invalid = (
            (ExceptionCase.Status.OPEN, ExceptionCase.Status.CLOSED),
            (ExceptionCase.Status.OPEN, ExceptionCase.Status.RESOLVED),
            (ExceptionCase.Status.WAITING, ExceptionCase.Status.CLOSED),
            (ExceptionCase.Status.CLOSED, ExceptionCase.Status.RESOLVED),
            (ExceptionCase.Status.OPEN, ExceptionCase.Status.PENDING),
            (ExceptionCase.Status.OPEN, ExceptionCase.Status.OPEN),
        )
        for source, target in invalid:
            with self.subTest(source=source, target=target):
                case = self.case(source)
                with self.assertRaises(CaseTransitionError):
                    self.transition(case, target)
        self.assertEqual(CaseActivity.objects.count(), 0)

    def test_viewer_or_anonymous_cannot_transition(self):
        viewer = get_user_model().objects.create_user(username="case-viewer", password="safe-password")
        case = self.case()
        with self.assertRaises(PermissionDenied):
            transition_case(case.pk, ExceptionCase.Status.IN_PROGRESS, actor=viewer)
        with self.assertRaises(PermissionDenied):
            transition_case(case.pk, ExceptionCase.Status.IN_PROGRESS, actor=None)

    def test_activity_failure_rolls_back_case_state(self):
        case = self.case()
        with patch("email_exceptions.case_workflow.CaseActivity.objects.using", side_effect=RuntimeError("activity failure")):
            with self.assertRaises(RuntimeError):
                self.transition(case, ExceptionCase.Status.IN_PROGRESS)
        case.refresh_from_db()
        self.assertEqual(case.status, ExceptionCase.Status.OPEN)
        self.assertEqual(CaseActivity.objects.count(), 0)

    def test_only_case_workflow_changes_lifecycle_state(self):
        case = self.case()
        self.assertEqual(case.status, ExceptionCase.Status.OPEN)
        self.transition(case, ExceptionCase.Status.IN_PROGRESS)
        self.assertEqual(CaseMessage.objects.count(), 0)
        self.assertEqual(EmailException.objects.count(), 0)

    def test_legacy_pending_is_not_reinterpreted_and_can_enter_workflow_explicitly(self):
        case = self.case(ExceptionCase.Status.PENDING)
        self.transition(case, ExceptionCase.Status.IN_PROGRESS)
        self.assertEqual(ExceptionCase.objects.get(pk=case.pk).status, ExceptionCase.Status.IN_PROGRESS)

    def test_http_transition_is_post_only_and_requires_operator(self):
        case = self.case()
        client = Client()
        client.force_login(self.user)
        response = client.get(f"/operaciones/excepciones-correo/casos/{case.pk}/status/")
        self.assertEqual(response.status_code, 405)

        viewer = get_user_model().objects.create_user(username="workflow-viewer", password="safe-password")
        viewer_client = Client()
        viewer_client.force_login(viewer)
        response = viewer_client.post(
            f"/operaciones/excepciones-correo/casos/{case.pk}/status/",
            {"status": ExceptionCase.Status.IN_PROGRESS},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(ExceptionCase.objects.get(pk=case.pk).status, ExceptionCase.Status.OPEN)

    def test_http_transition_uses_csrf_and_domain_service(self):
        case = self.case()
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        url = f"/operaciones/excepciones-correo/casos/{case.pk}/status/"
        self.assertEqual(client.post(url, {"status": ExceptionCase.Status.IN_PROGRESS}).status_code, 403)
        response = client.post(url, {"status": ExceptionCase.Status.IN_PROGRESS}, HTTP_X_CSRFTOKEN="missing")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(ExceptionCase.objects.get(pk=case.pk).status, ExceptionCase.Status.OPEN)

        client = Client()
        client.force_login(self.user)
        response = client.post(url, {"status": ExceptionCase.Status.IN_PROGRESS})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(ExceptionCase.objects.get(pk=case.pk).status, ExceptionCase.Status.IN_PROGRESS)
