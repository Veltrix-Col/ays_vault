import threading
import time

from django.contrib.auth import get_user_model
from django.test import TestCase, TransactionTestCase, override_settings

from .models import NotificationRecipient, NotificationRecord, SecurityAlert, UserProfile
from .notifications import notify_alert_async, notify_alert_by_id
from .security import audit
from .tasks import run_async


class RunAsyncTests(TestCase):
    def test_run_async_executes_in_background_without_blocking_caller(self):
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

    def make_alert(self):
        event = audit(None, "ACCESS", user=self.user, metadata={"safe": True})
        return SecurityAlert.objects.create(event=event, alert_type="ASYNC_TEST", severity="HIGH", affected_user=self.user, description="Evento seguro")

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
