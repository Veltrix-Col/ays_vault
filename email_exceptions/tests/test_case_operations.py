from unittest.mock import patch
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import PermissionDenied
from django.test import Client, TestCase
from django.utils import timezone

from email_exceptions.case_workflow import (
    CaseOperationError,
    add_case_note,
    assign_case,
    get_assignable_operators,
    take_case,
    unassign_case,
    update_case_follow_up,
)
from email_exceptions.models import CaseActivity, CaseMessage, EmailException, ExceptionCase


class CaseOperationsTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.operator = user_model.objects.create_user(username="operator-a", password="safe")
        self.second_operator = user_model.objects.create_user(username="operator-b", password="safe")
        self.viewer = user_model.objects.create_user(username="viewer", password="safe")
        operate = Permission.objects.get(content_type__app_label="email_exceptions", codename="operate_email_exceptions")
        view = Permission.objects.get(content_type__app_label="email_exceptions", codename="view_email_exceptions_operational")
        self.operator.user_permissions.add(operate, view)
        self.second_operator.user_permissions.add(operate, view)
        self.viewer.user_permissions.add(view)

    def case(self, status=ExceptionCase.Status.OPEN):
        now = timezone.now()
        return ExceptionCase.objects.create(
            case_key=f"operations-{ExceptionCase.objects.count()}-{status}",
            status=status,
            opened_at=now,
            last_activity_at=now,
        )

    def test_assign_records_assignee_timestamp_and_activity(self):
        case = self.case()
        before = timezone.now()
        updated, activity = assign_case(case.pk, actor=self.operator, assignee_id=self.second_operator.pk)
        updated.refresh_from_db()
        self.assertEqual(updated.assigned_to_id, self.second_operator.pk)
        self.assertGreaterEqual(updated.assigned_at, before)
        self.assertEqual(activity.event_type, CaseActivity.EventType.ASSIGNED)
        self.assertEqual(activity.actor_id, self.operator.pk)
        self.assertEqual(activity.metadata, {"from_user_id": None, "to_user_id": self.second_operator.pk})

    def test_assign_reassigns_and_same_assignee_is_noop(self):
        case = self.case()
        assign_case(case.pk, actor=self.operator, assignee_id=self.operator.pk)
        _, activity = assign_case(case.pk, actor=self.second_operator, assignee_id=self.second_operator.pk)
        self.assertEqual(activity.event_type, CaseActivity.EventType.REASSIGNED)
        self.assertEqual(activity.metadata, {"from_user_id": self.operator.pk, "to_user_id": self.second_operator.pk})
        count = CaseActivity.objects.count()
        _, no_op = assign_case(case.pk, actor=self.operator, assignee_id=self.second_operator.pk)
        self.assertIsNone(no_op)
        self.assertEqual(CaseActivity.objects.count(), count)

    def test_inactive_assignees_are_rejected_and_active_identities_are_allowed(self):
        inactive = get_user_model().objects.create_user(username="inactive", password="safe", is_active=False)
        with self.assertRaises(CaseOperationError):
            assign_case(self.case().pk, actor=self.operator, assignee_id=inactive.pk)
        assigned, _activity = assign_case(self.case().pk, actor=self.operator, assignee_id=self.viewer.pk)
        self.assertEqual(assigned.assigned_to_id, self.viewer.pk)
        with self.assertRaises(PermissionDenied):
            assign_case(self.case().pk, actor=self.viewer, assignee_id=self.operator.pk)
        with self.assertRaises(PermissionDenied):
            assign_case(self.case().pk, actor=None, assignee_id=self.operator.pk)

    def test_take_case_is_explicit_and_does_not_change_status(self):
        case = self.case()
        _, activity = take_case(case.pk, actor=self.operator)
        case.refresh_from_db()
        self.assertEqual(case.assigned_to_id, self.operator.pk)
        self.assertEqual(case.status, ExceptionCase.Status.OPEN)
        self.assertEqual(activity.event_type, CaseActivity.EventType.ASSIGNED)
        count = CaseActivity.objects.count()
        _, no_op = take_case(case.pk, actor=self.operator)
        self.assertIsNone(no_op)
        self.assertEqual(CaseActivity.objects.count(), count)
        with self.assertRaises(CaseOperationError):
            take_case(case.pk, actor=self.second_operator)

    def test_unassign_clears_assignment_and_is_noop_when_free(self):
        case = self.case()
        take_case(case.pk, actor=self.operator)
        _, activity = unassign_case(case.pk, actor=self.second_operator)
        case.refresh_from_db()
        self.assertIsNone(case.assigned_to_id)
        self.assertIsNone(case.assigned_at)
        self.assertEqual(activity.event_type, CaseActivity.EventType.UNASSIGNED)
        self.assertEqual(activity.metadata, {"from_user_id": self.operator.pk, "to_user_id": None})
        count = CaseActivity.objects.count()
        _, no_op = unassign_case(case.pk, actor=self.operator)
        self.assertIsNone(no_op)
        self.assertEqual(CaseActivity.objects.count(), count)

    def test_assignable_operators_include_direct_and_group_permission_only(self):
        group = Group.objects.create(name="email operators")
        operate = Permission.objects.get(content_type__app_label="email_exceptions", codename="operate_email_exceptions")
        group.permissions.add(operate)
        grouped = get_user_model().objects.create_user(username="grouped", password="safe")
        grouped.groups.add(group)
        inactive = get_user_model().objects.create_user(username="inactive-op", password="safe", is_active=False)
        inactive.groups.add(group)
        ids = set(get_assignable_operators().values_list("pk", flat=True))
        self.assertIn(self.operator.pk, ids)
        self.assertIn(self.second_operator.pk, ids)
        self.assertIn(grouped.pk, ids)
        self.assertIn(self.viewer.pk, ids)
        self.assertNotIn(inactive.pk, ids)
        self.assertEqual(len(ids), len(get_assignable_operators()))

    def test_follow_up_updates_both_values_and_audits_previous_and_new(self):
        case = self.case()
        follow_up = timezone.now() + timedelta(days=1)
        _, activity = update_case_follow_up(case.pk, actor=self.operator, next_action="Validar soporte", follow_up_at=follow_up)
        case.refresh_from_db()
        self.assertEqual(case.next_action, "Validar soporte")
        self.assertEqual(case.follow_up_at, follow_up)
        self.assertEqual(activity.event_type, CaseActivity.EventType.FOLLOW_UP_UPDATED)
        self.assertEqual(activity.metadata["previous_next_action"], "")
        self.assertEqual(activity.metadata["new_next_action"], "Validar soporte")
        self.assertEqual(activity.metadata["new_follow_up_at"], follow_up.isoformat())

    def test_follow_up_noop_clear_and_closed_states(self):
        case = self.case()
        follow_up = timezone.now() + timedelta(hours=2)
        update_case_follow_up(case.pk, actor=self.operator, next_action="Llamar", follow_up_at=follow_up)
        count = CaseActivity.objects.count()
        _, no_op = update_case_follow_up(case.pk, actor=self.operator, next_action="Llamar", follow_up_at=follow_up)
        self.assertIsNone(no_op)
        self.assertEqual(CaseActivity.objects.count(), count)
        _, cleared = update_case_follow_up(case.pk, actor=self.operator, next_action="", follow_up_at=None)
        self.assertEqual(cleared.event_type, CaseActivity.EventType.FOLLOW_UP_UPDATED)
        case.status = ExceptionCase.Status.RESOLVED
        case.save(update_fields=("status",))
        with self.assertRaises(CaseOperationError):
            update_case_follow_up(case.pk, actor=self.operator, next_action="No permitido", follow_up_at=None)

    def test_follow_up_length_is_validated_server_side(self):
        with self.assertRaises(CaseOperationError):
            update_case_follow_up(self.case().pk, actor=self.operator, next_action="x" * 501, follow_up_at=None)

    def test_note_is_append_only_activity_and_not_a_message_or_exception(self):
        case = self.case()
        _, activity = add_case_note(case.pk, actor=self.operator, note="Confirmar documento recibido")
        self.assertEqual(activity.event_type, CaseActivity.EventType.NOTE_ADDED)
        self.assertEqual(activity.actor_id, self.operator.pk)
        self.assertEqual(activity.metadata, {"note": "Confirmar documento recibido"})
        self.assertEqual(CaseMessage.objects.count(), 0)
        self.assertEqual(EmailException.objects.count(), 0)
        with self.assertRaises(CaseOperationError):
            add_case_note(case.pk, actor=self.operator, note="   ")
        with self.assertRaises(CaseOperationError):
            add_case_note(case.pk, actor=self.operator, note="x" * 4001)
        with self.assertRaises(PermissionDenied):
            add_case_note(case.pk, actor=self.viewer, note="No permitido")

    def test_mutations_and_activities_roll_back_together(self):
        case = self.case()
        with patch("email_exceptions.case_workflow.CaseActivity.objects.using", side_effect=RuntimeError("activity failure")):
            with self.assertRaises(RuntimeError):
                take_case(case.pk, actor=self.operator)
        case.refresh_from_db()
        self.assertIsNone(case.assigned_to_id)
        self.assertEqual(CaseActivity.objects.count(), 0)

    def test_stale_instance_is_resolved_against_locked_database_state(self):
        case = self.case()
        stale = ExceptionCase.objects.get(pk=case.pk)
        ExceptionCase.objects.filter(pk=case.pk).update(status=ExceptionCase.Status.WAITING)
        updated, _ = take_case(stale.pk, actor=self.operator)
        self.assertEqual(updated.status, ExceptionCase.Status.WAITING)

    def test_http_operations_are_post_only_authorized_and_csrf_protected(self):
        case = self.case()
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.operator)
        self.assertEqual(client.get(f"/operaciones/excepciones-correo/casos/{case.pk}/take/").status_code, 405)
        self.assertEqual(client.post(f"/operaciones/excepciones-correo/casos/{case.pk}/take/").status_code, 403)
        viewer_client = Client()
        viewer_client.force_login(self.viewer)
        self.assertEqual(viewer_client.post(f"/operaciones/excepciones-correo/casos/{case.pk}/take/").status_code, 403)
        authorized_client = Client()
        authorized_client.force_login(self.operator)
        response = authorized_client.post(f"/operaciones/excepciones-correo/casos/{case.pk}/take/")
        self.assertEqual(response.status_code, 302)
        case.refresh_from_db()
        self.assertEqual(case.assigned_to_id, self.operator.pk)

    def test_case_detail_shows_operational_controls_only_to_operator(self):
        case = self.case()
        viewer_client = Client()
        viewer_client.force_login(self.viewer)
        viewer_response = viewer_client.get(f"/operaciones/excepciones-correo/casos/{case.pk}/")
        self.assertEqual(viewer_response.status_code, 200)
        self.assertNotContains(viewer_response, "Tomar caso")
        operator_client = Client()
        operator_client.force_login(self.operator)
        operator_response = operator_client.get(f"/operaciones/excepciones-correo/casos/{case.pk}/")
        self.assertContains(operator_response, "Tomar caso")
        self.assertContains(operator_response, "Nota interna")

    def test_http_follow_up_and_note_use_post_contract(self):
        case = self.case()
        client = Client()
        client.force_login(self.operator)
        follow_up_url = f"/operaciones/excepciones-correo/casos/{case.pk}/follow-up/"
        response = client.post(follow_up_url, {"next_action": "Contactar al cliente", "follow_up_at": "2026-10-01T09:30"})
        self.assertEqual(response.status_code, 302)
        case.refresh_from_db()
        self.assertEqual(case.next_action, "Contactar al cliente")
        self.assertEqual(timezone.localtime(case.follow_up_at).strftime("%Y-%m-%d %H:%M"), "2026-10-01 09:30")
        note_url = f"/operaciones/excepciones-correo/casos/{case.pk}/notes/"
        response = client.post(note_url, {"note": "Documento solicitado"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(CaseActivity.objects.get(event_type=CaseActivity.EventType.NOTE_ADDED).metadata["note"], "Documento solicitado")
