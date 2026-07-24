import threading
import time
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, connections, transaction
from django.test import Client, TransactionTestCase, override_settings
from django.urls import reverse
from django_otp.oath import TOTP
from django_otp.plugins.otp_totp.models import TOTPDevice

from .models import (
    AuditEvent,
    NotificationRecord,
    NotificationRecipient,
    PolicyEvaluationRun,
    SecureSession,
    SecurityAlert,
    UserProfile,
)
from .notifications import _audit_alert_delivery
from .security import audit, verify_audit_chain
from .tasks import run_async


PASSWORD = "ConcurrencyTest123!"
TEST_SETTINGS = {
    "APP_ENV": "development",
    "FIELD_ENCRYPTION_KEY": "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
    "FIELD_FINGERPRINT_KEY": "mfa-concurrency-test-key",
    "PASSWORD_HASHERS": ["django.contrib.auth.hashers.MD5PasswordHasher"],
    "ALERT_EMAIL_BACKEND": "console",
    "EMAIL_BACKEND": "django.core.mail.backends.locmem.EmailBackend",
    "ALERT_EMAIL_ADMIN": "",
    "ALERT_EMAIL_LEADER": "",
}


@override_settings(**TEST_SETTINGS)
class MFASQLiteConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.user = get_user_model().objects.create_user("mfa.concurrency", password=PASSWORD)
        profile = self.user.vault_profile
        profile.role = UserProfile.LEADER
        profile.active = True
        profile.mfa_enabled = True
        profile.mfa_status = UserProfile.MFA_ACTIVE
        profile.save()
        self.device = TOTPDevice.objects.create(user=self.user, name="Prueba", confirmed=True)
        NotificationRecipient.objects.create(
            name="Administrador de prueba",
            email="admin@example.invalid",
            active=True,
            is_primary=True,
            minimum_severity="LOW",
        )

    def token(self):
        self.device.refresh_from_db()
        self.device.last_t = -1
        self.device.throttling_failure_timestamp = None
        self.device.throttling_failure_count = 0
        self.device.save()
        totp = TOTP(
            self.device.bin_key,
            self.device.step,
            self.device.t0,
            self.device.digits,
            self.device.drift,
        )
        totp.time = time.time()
        return str(totp.token()).zfill(self.device.digits)

    def begin_login(self, client):
        response = client.post(reverse("login"), {"username": self.user.username, "password": PASSWORD})
        self.assertRedirects(response, reverse("mfa_verify"), fetch_redirect_response=False)

    def complete_login(self, client):
        return client.post(reverse("mfa_verify"), {"token": self.token(), "recovery_code": ""})

    def test_mfa_session_replacement_does_not_race_notification_audit(self):
        first = Client()
        with patch("vault.notifications.notify_alert_async"):
            self.begin_login(first)
            self.assertEqual(self.complete_login(first).status_code, 302)

        second = Client()
        self.begin_login(second)

        lock_acquired = threading.Event()
        release_lock = threading.Event()
        callback_states = []
        original_delivery_audit = _audit_alert_delivery

        def hold_sqlite_write_lock(record, alert):
            with transaction.atomic():
                PolicyEvaluationRun.objects.create(dry_run=True, status="LOCK_TEST")
                lock_acquired.set()
                release_lock.wait(timeout=2)
            original_delivery_audit(record, alert)

        def coordinated_run_async(func, *args, **kwargs):
            callback_states.append(
                {
                    "in_atomic_block": connection.in_atomic_block,
                    "session_created": AuditEvent.objects.filter(user=self.user, action="SESSION_CREATED").exists(),
                    "mfa_success": AuditEvent.objects.filter(user=self.user, action="MFA_SUCCESS").exists(),
                }
            )
            result = run_async(func, *args, **kwargs)
            self.assertTrue(lock_acquired.wait(timeout=2), "la notificación no alcanzó la escritura concurrente")
            return result

        timer = threading.Timer(1, release_lock.set)
        timer.start()
        try:
            if connection.vendor == "sqlite":
                with connection.cursor() as cursor:
                    cursor.execute("PRAGMA busy_timeout = 200")
            with (
                patch("vault.notifications.run_async", side_effect=coordinated_run_async),
                patch("vault.notifications._audit_alert_delivery", side_effect=hold_sqlite_write_lock),
            ):
                response = self.complete_login(second)
        finally:
            release_lock.set()
            timer.join(timeout=2)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(SecureSession.objects.filter(user=self.user, status=SecureSession.ACTIVE).count(), 1)
        self.assertTrue(AuditEvent.objects.filter(user=self.user, action="SESSION_REPLACED").exists())
        self.assertTrue(AuditEvent.objects.filter(user=self.user, action="SESSION_CREATED").exists())
        self.assertTrue(AuditEvent.objects.filter(user=self.user, action="MFA_SUCCESS").exists())
        self.assertTrue(callback_states)
        self.assertTrue(
            all(
                state == {"in_atomic_block": False, "session_created": True, "mfa_success": True}
                for state in callback_states
            )
        )
        replacement_alerts = SecurityAlert.objects.filter(
            affected_user=self.user,
            alert_type="SESSION_REPLACED",
        )
        self.assertEqual(replacement_alerts.count(), 1)
        self.assertEqual(NotificationRecord.objects.filter(alert=replacement_alerts.get()).count(), 1)
        self.assertTrue(AuditEvent.objects.filter(user=self.user, action="EMAIL_SENT").exists())

    def test_sqlite_serializes_concurrent_audit_writers_without_lock_errors(self):
        if connection.vendor != "sqlite":
            self.skipTest("Endurecimiento específico del entorno SQLite de desarrollo")

        barrier = threading.Barrier(3)
        errors = []

        def write_event(index):
            close_old_connections()
            try:
                user = get_user_model().objects.get(pk=self.user.pk)
                barrier.wait(timeout=2)
                audit(None, "ACCESS", user=user, metadata={"writer": index})
            except Exception as exc:  # La aserción conserva el error real.
                errors.append(exc)
            finally:
                connections.close_all()

        threads = [
            threading.Thread(target=write_event, args=(index,))
            for index in (1, 2)
        ]
        for thread in threads:
            thread.start()
        barrier.wait(timeout=2)
        for thread in threads:
            thread.join(timeout=5)

        self.assertFalse(errors, errors)
        self.assertEqual(
            AuditEvent.objects.filter(
                user=self.user,
                action="ACCESS",
                metadata__writer__in=[1, 2],
            ).count(),
            2,
        )
        self.assertTrue(verify_audit_chain()[0])

    def test_sqlite_uses_immediate_transactions_and_bounded_wait(self):
        if connection.vendor != "sqlite":
            self.skipTest("Configuración específica del entorno SQLite")
        options = connection.settings_dict["OPTIONS"]
        self.assertEqual(options["transaction_mode"], "IMMEDIATE")
        self.assertGreaterEqual(options["timeout"], 10)
