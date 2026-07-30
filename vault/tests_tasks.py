import threading
import time
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, TransactionTestCase, override_settings

from .models import NotificationRecipient, NotificationRecord, SecurityAlert, UserProfile
from .notifications import configured_recipients, notify_alert_async, notify_alert_by_id, send_alert_notification
from .security import audit
from .tasks import run_async


class RunAsyncTests(TestCase):
    @patch("vault.tasks._use_background_thread", return_value=True)
    def test_run_async_executes_in_background_without_blocking_caller(self, _threaded):
        started = threading.Event()
        release = threading.Event()
        finished = threading.Event()

        def slow_task():
            started.set()
            release.wait(timeout=2)
            finished.set()

        run_async(slow_task)
        self.assertTrue(started.wait(timeout=2), "la tarea nunca inició en segundo plano")
        self.assertFalse(finished.is_set(), "run_async bloqueó al llamador en vez de ejecutar en segundo plano")
        release.set()
        self.assertTrue(finished.wait(timeout=2), "la tarea en segundo plano nunca terminó")

    @patch("vault.tasks._use_background_thread", return_value=False)
    def test_run_async_serializes_work_for_sqlite(self, _threaded):
        caller = threading.get_ident()
        executed_by = []

        run_async(lambda: executed_by.append(threading.get_ident()))

        self.assertEqual(executed_by, [caller])

    def test_current_database_selects_safe_execution_mode(self):
        from .tasks import _use_background_thread

        self.assertEqual(_use_background_thread(), connection.vendor != "sqlite")

    @patch("vault.tasks.connections.close_all")
    @patch("vault.tasks.close_old_connections")
    def test_worker_refreshes_and_closes_thread_connections(self, close_old, close_all):
        from .tasks import _run_safely

        _run_safely(lambda: None, (), {})

        close_old.assert_called_once_with()
        close_all.assert_called_once_with()

    def test_run_async_swallows_exceptions_without_raising(self):
        finished = threading.Event()

        def failing_task():
            finished.set()
            raise RuntimeError("boom")

        run_async(failing_task)
        self.assertTrue(finished.wait(timeout=2))


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class NotifyAlertAsyncTests(TransactionTestCase):
    """Usa TransactionTestCase: notify_alert_async corre en otro hilo con su propia conexión
    y necesita que los datos del setUp ya estén realmente confirmados para poder verlos."""

    def setUp(self):
        self.user = get_user_model().objects.create_user("lider.tasks", password="ControlCenter123!")
        self.user.vault_profile.role = UserProfile.LEADER
        self.user.vault_profile.active = True
        self.user.vault_profile.save()
        NotificationRecipient.objects.create(name="Admin demo", email="admin@example.invalid", active=True, is_primary=True, minimum_severity="LOW")

    def make_alert(self, *, action="LOGIN", outside_office_hours=True):
        # El fixture controla explícitamente el estado horario. Evita que la
        # hora real de ejecución dispare la notificación automática antes de
        # que el caso de prueba termine de construir su alerta.
        with patch("vault.security.outside_hours", return_value=False):
            event = audit(None, action, user=self.user, metadata={"safe": True})
        if event.outside_office_hours != outside_office_hours:
            event.outside_office_hours = outside_office_hours
            event.save(update_fields=["outside_office_hours"])
        return SecurityAlert.objects.create(
            event=event,
            alert_type="ASYNC_TEST",
            severity="HIGH",
            affected_user=self.user,
            description="Evento seguro",
        )

    def test_notify_alert_async_delivers_notification_via_background_thread(self):
        alert = self.make_alert()
        notify_alert_async(alert)
        deadline = time.monotonic() + 3
        record = None
        while time.monotonic() < deadline:
            record = NotificationRecord.objects.filter(alert=alert).first()
            if record and record.result != NotificationRecord.PENDING:
                break
            time.sleep(0.05)
        self.assertIsNotNone(record, "notify_alert_async no generó un NotificationRecord a tiempo")
        self.assertEqual(record.result, NotificationRecord.SENT)

    def test_notify_alert_by_id_ignores_missing_alert(self):
        self.assertEqual(notify_alert_by_id(999999), [])

    def test_automatic_email_is_not_sent_for_normal_or_non_whitelisted_events(self):
        normal_login = self.make_alert(outside_office_hours=False)
        normal_reveal = self.make_alert(action="REVEAL", outside_office_hours=False)
        card_change = self.make_alert(action="CREATE", outside_office_hours=True)
        copy_event = self.make_alert(action="COPY", outside_office_hours=True)
        self.assertEqual(notify_alert_by_id(normal_login.pk), [])
        self.assertEqual(notify_alert_by_id(normal_reveal.pk), [])
        self.assertEqual(notify_alert_by_id(card_change.pk), [])
        self.assertEqual(notify_alert_by_id(copy_event.pk), [])
        self.assertFalse(
            NotificationRecord.objects.filter(
                alert__in=[normal_login, normal_reveal, card_change, copy_event]
            ).exists()
        )

    def test_all_active_recipients_receive_allowed_alerts_without_legacy_filters(self):
        NotificationRecipient.objects.create(
            name="Destinatario heredado",
            email="segundo@example.invalid",
            active=True,
            delivery_mode=NotificationRecipient.WEEKLY,
            alert_types=["UNRELATED"],
            minimum_severity="CRITICAL",
        )
        alert = self.make_alert(action="REVEAL", outside_office_hours=True)
        self.assertEqual(
            configured_recipients(alert),
            ["admin@example.invalid", "segundo@example.invalid"],
        )

    @patch("vault.notifications.send_notification")
    def test_allowed_alerts_use_professional_cardmanager_subjects(self, send_mock):
        login_alert = self.make_alert(action="LOGIN", outside_office_hours=True)
        reveal_alert = self.make_alert(action="REVEAL", outside_office_hours=True)
        send_alert_notification(login_alert, "admin@example.invalid")
        self.assertEqual(
            send_mock.call_args.kwargs["subject"],
            "CardManager | Inicio de sesión fuera del horario permitido",
        )
        send_alert_notification(reveal_alert, "admin@example.invalid")
        self.assertEqual(
            send_mock.call_args.kwargs["subject"],
            "CardManager | Revelado de información fuera del horario permitido",
        )
        self.assertNotIn("4111111111111111", send_mock.call_args.kwargs["html_body"])
