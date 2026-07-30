from datetime import datetime, time, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django_otp.oath import TOTP
from django_otp.plugins.otp_totp.models import TOTPDevice

from .identity import grant_reauthentication
from .models import AccessException, AlertTransition, AuditEvent, Holiday, NotificationRecord, PaymentCard, PolicyConfiguration, PolicyEvaluationRun, SecurityAlert, SensitiveOperationWindow, UserProfile
from .notifications import send_alert_notification
from .policies import evaluate_access_policy, invalidate_policy_cache
from .policy_evaluation import evaluate_security_policies
from .security import audit, session_hash, verify_audit_chain


PASSWORD = "ControlCenter123!"
TEST_KEYS = dict(APP_ENV="development", FIELD_ENCRYPTION_KEY="MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=", FIELD_FINGERPRINT_KEY="control-center-test-key")


@override_settings(**TEST_KEYS, PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class PolicyAndScheduleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_user("admin.control", password=PASSWORD)
        cls.admin.vault_profile.role = UserProfile.ADMIN; cls.admin.vault_profile.active = True; cls.admin.vault_profile.mfa_enabled = True; cls.admin.vault_profile.mfa_status = UserProfile.MFA_ACTIVE; cls.admin.vault_profile.save()

    def setUp(self):
        invalidate_policy_cache()
        self.policy, _ = PolicyConfiguration.objects.get_or_create(singleton=1)
        self.policy.weekday_start = time(7); self.policy.weekday_end = time(18); self.policy.saturday_enabled = False; self.policy.sunday_enabled = False; self.policy.outside_hours_behavior = "BLOCK"; self.policy.save()
        invalidate_policy_cache()

    def at(self, year, month, day, hour):
        return datetime(year, month, day, hour, tzinfo=ZoneInfo("America/Bogota"))

    def test_weekday_allowed_and_blocked(self):
        self.assertTrue(evaluate_access_policy(self.admin, operation="LOGIN", at=self.at(2026, 7, 13, 10)).allowed)
        blocked = evaluate_access_policy(self.admin, operation="LOGIN", at=self.at(2026, 7, 13, 22))
        self.assertFalse(blocked.allowed); self.assertTrue(blocked.requires_block)

    def test_saturday_and_sunday_are_evaluated(self):
        self.assertFalse(evaluate_access_policy(self.admin, operation="VIEW", at=self.at(2026, 7, 18, 10)).allowed)
        self.assertFalse(evaluate_access_policy(self.admin, operation="VIEW", at=self.at(2026, 7, 19, 10)).allowed)
        self.policy.saturday_enabled = True; self.policy.saturday_start = time(8); self.policy.saturday_end = time(12); self.policy.save(); invalidate_policy_cache()
        self.assertTrue(evaluate_access_policy(self.admin, operation="VIEW", at=self.at(2026, 7, 18, 10)).allowed)

    def test_holiday_blocks_without_internet(self):
        Holiday.objects.create(date=datetime(2026, 7, 20).date(), name="Independencia")
        decision = evaluate_access_policy(self.admin, operation="LOGIN", at=self.at(2026, 7, 20, 10))
        self.assertFalse(decision.allowed); self.assertIn("Festivo", decision.reason)

    def test_active_exception_overrides_schedule(self):
        moment = self.at(2026, 7, 19, 10)
        exception = AccessException.objects.create(name="Domingo", exception_type=AccessException.ALLOW, user=self.admin, starts_at=moment-timedelta(hours=1), ends_at=moment+timedelta(hours=1), operations=["LOGIN"], reason="Operacion extraordinaria", created_by=self.admin)
        decision = evaluate_access_policy(self.admin, operation="LOGIN", at=moment)
        self.assertTrue(decision.allowed); self.assertEqual(decision.exception_applied, exception.pk)

    def test_expired_and_wrong_user_exception_do_not_apply(self):
        other = get_user_model().objects.create_user("other.control")
        moment = self.at(2026, 7, 19, 10)
        AccessException.objects.create(name="Otra persona", exception_type=AccessException.ALLOW, user=other, starts_at=moment-timedelta(hours=1), ends_at=moment+timedelta(hours=1), operations=["LOGIN"], reason="Solo otro usuario", created_by=self.admin)
        AccessException.objects.create(name="Expirada", exception_type=AccessException.ALLOW, user=self.admin, starts_at=moment-timedelta(days=2), ends_at=moment-timedelta(days=1), operations=["LOGIN"], reason="Ya expiro", created_by=self.admin, status=AccessException.EXPIRED)
        self.assertFalse(evaluate_access_policy(self.admin, operation="LOGIN", at=moment).allowed)

    def test_role_exception_applies(self):
        moment = self.at(2026, 7, 19, 10)
        AccessException.objects.create(name="Administradores", exception_type=AccessException.ALLOW, role=UserProfile.ADMIN, starts_at=moment-timedelta(hours=1), ends_at=moment+timedelta(hours=1), operations=["LOGIN"], reason="Continuidad", created_by=self.admin)
        self.assertTrue(evaluate_access_policy(self.admin, role=UserProfile.ADMIN, operation="LOGIN", at=moment).allowed)


@override_settings(**TEST_KEYS, PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"], ALERT_EMAIL_BACKEND="console", EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", ALERT_EMAIL_ADMIN="admin@example.invalid", ALERT_EMAIL_LEADER="", ALERT_EMAIL_FROM="vault@example.invalid", VAULT_BASE_URL="https://vault.example.invalid")
class AlertNotificationAndCommandTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user("lider.control", password=PASSWORD)
        cls.user.vault_profile.role = UserProfile.LEADER; cls.user.vault_profile.active = True; cls.user.vault_profile.mfa_enabled = True; cls.user.vault_profile.mfa_status = UserProfile.MFA_ACTIVE; cls.user.vault_profile.save()
        cls.card = PaymentCard(client_name="Cliente seguro", cardholder_name="Titular", brand="VISA", purpose="Prueba", created_by=cls.user)
        cls.card.set_pan("4111111111111111"); cls.card.set_expiry("12/29"); cls.card.save()

    def setUp(self):
        invalidate_policy_cache(); PolicyConfiguration.objects.get_or_create(singleton=1)

    def make_alert(self, kind="CRITICAL_TEST", severity="CRITICAL"):
        event = audit(None, "ACCESS", user=self.user, metadata={"safe": True})
        return SecurityAlert.objects.create(event=event, alert_type=kind, severity=severity, affected_user=self.user, description="Evento seguro", recommendation="Revisar")

    def test_critical_alert_sends_console_compatible_email_and_history(self):
        alert = self.make_alert()
        record = send_alert_notification(alert, "admin@example.invalid")
        self.assertEqual(record.result, NotificationRecord.SENT)
        self.assertEqual(record.masked_recipient, "a***@example.invalid")

    def test_email_idempotency_avoids_duplicate(self):
        alert = self.make_alert()
        first = send_alert_notification(alert, "admin@example.invalid")
        second = send_alert_notification(alert, "admin@example.invalid")
        self.assertEqual(first.pk, second.pk); self.assertEqual(NotificationRecord.objects.count(), 1)

    def test_email_excludes_pan_expiry_and_secrets(self):
        from django.core import mail
        alert = self.make_alert()
        send_alert_notification(alert, "admin@example.invalid")
        body = mail.outbox[0].body + str(mail.outbox[0].alternatives)
        self.assertNotIn("4111111111111111", body); self.assertNotIn("12/29", body); self.assertNotIn("CLIENT_SECRET", body)

    def test_email_failure_does_not_raise_and_can_retry(self):
        class FailureBackend:
            name = "failure"
            def send(self, *args): raise RuntimeError("SIMULATED_FAILURE")
        alert = self.make_alert()
        with patch("vault.notifications.get_backend", return_value=FailureBackend()):
            record = send_alert_notification(alert, "admin@example.invalid")
        self.assertIn(record.result, {NotificationRecord.RETRY, NotificationRecord.FAILED})
        record = send_alert_notification(alert, "admin@example.invalid", force_retry=True)
        self.assertEqual(record.result, NotificationRecord.SENT)

    def test_inactivity_alert_is_cautious_and_idempotent(self):
        result = evaluate_security_policies(now=timezone.now())
        alert = SecurityAlert.objects.get(alert_type="POSSIBLE_PARALLEL_TOOL_USE")
        self.assertNotIn("Estan usando Excel", alert.description)
        second = evaluate_security_policies(now=timezone.now())
        self.assertGreaterEqual(second["existing"], 1)

    def test_recent_use_prevents_parallel_tool_alert(self):
        audit(None, "VIEW", user=self.user, card=self.card)
        evaluate_security_policies(now=timezone.now())
        self.assertFalse(SecurityAlert.objects.filter(alert_type="POSSIBLE_PARALLEL_TOOL_USE").exists())

    def test_vacation_exception_prevents_false_positive(self):
        now = timezone.now()
        AccessException.objects.create(name="Vacaciones", exception_type=AccessException.VACATION, starts_at=now-timedelta(days=1), ends_at=now+timedelta(days=1), reason="Vacaciones programadas", created_by=self.user)
        evaluate_security_policies(now=now)
        self.assertFalse(SecurityAlert.objects.filter(alert_type="POSSIBLE_PARALLEL_TOOL_USE").exists())

    def test_inactive_user_generates_alert(self):
        evaluate_security_policies(now=timezone.now())
        self.assertTrue(SecurityAlert.objects.filter(alert_type="INACTIVE_USER", affected_user=self.user).exists())

    def test_command_dry_run_records_success_without_alerts(self):
        call_command("evaluate_security_policies", "--dry-run")
        self.assertEqual(PolicyEvaluationRun.objects.latest("started_at").status, "SUCCESS")
        self.assertFalse(SecurityAlert.objects.filter(alert_type="POSSIBLE_PARALLEL_TOOL_USE").exists())

    def test_audit_chain_remains_valid_after_command(self):
        call_command("evaluate_security_policies", "--dry-run")
        self.assertTrue(verify_audit_chain()[0])

    def test_seed_demo_control_data_is_idempotent(self):
        call_command("seed_demo")
        counts = (
            PolicyConfiguration.objects.count(),
            AccessException.objects.count(),
            SecurityAlert.objects.count(),
            NotificationRecord.objects.count(),
        )
        call_command("seed_demo")
        self.assertEqual(
            counts,
            (
                PolicyConfiguration.objects.count(),
                AccessException.objects.count(),
                SecurityAlert.objects.count(),
                NotificationRecord.objects.count(),
            ),
        )
        self.assertTrue(Holiday.objects.exists())
        record = NotificationRecord.objects.get(notification_type="DEMO_NOTIFICATION")
        self.assertEqual(record.backend, "demo-console")
        self.assertEqual(record.external_id, "demo-no-external-delivery")
        self.assertEqual(record.masked_recipient, "a***********@example.invalid")


@override_settings(**TEST_KEYS, PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class ControlCenterPermissionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.admin = User.objects.create_user("admin.ui", password=PASSWORD)
        cls.leader = User.objects.create_user("leader.ui", password=PASSWORD)
        cls.analyst = User.objects.create_user("analyst.ui", password=PASSWORD)
        for user, role in [(cls.admin, UserProfile.ADMIN), (cls.leader, UserProfile.LEADER), (cls.analyst, UserProfile.ANALYST)]:
            profile = user.vault_profile; profile.role = role; profile.active = True; profile.mfa_enabled = True; profile.mfa_status = UserProfile.MFA_ACTIVE; profile.save()
            TOTPDevice.objects.create(user=user, confirmed=True)

    def token(self, user):
        device = TOTPDevice.objects.get(user=user); device.last_t = -1; device.save(update_fields=["last_t"])
        totp = TOTP(device.bin_key, device.step, device.t0, device.digits, device.drift); totp.time = timezone.now().timestamp()
        return str(totp.token()).zfill(device.digits)

    def setUp(self):
        invalidate_policy_cache()
        policy, _ = PolicyConfiguration.objects.get_or_create(singleton=1)
        policy.outside_hours_behavior = "ALLOW"
        policy.save(update_fields=["outside_hours_behavior"])
        invalidate_policy_cache()

    def login(self, user):
        client = Client(); client.post(reverse("login"), {"username": user.username, "password": PASSWORD}); client.post(reverse("mfa_verify"), {"token": self.token(user), "recovery_code": ""}); return client

    def test_admin_accesses_control_center_but_analyst_does_not(self):
        self.assertEqual(self.login(self.admin).get(reverse("vault:control_center")).status_code, 200)
        self.assertEqual(self.login(self.analyst).get(reverse("vault:control_center")).status_code, 403)

    def test_initial_route_and_menu_are_distinct_for_each_role(self):
        admin_client = self.login(self.admin)
        admin_start = admin_client.get(reverse("vault:dashboard"))
        self.assertRedirects(admin_start, reverse("vault:control_center"), fetch_redirect_response=False)
        admin_control = admin_client.get(reverse("vault:control_center"))
        self.assertContains(admin_control, ">Centro de Control<")
        self.assertNotContains(admin_control, ">Resumen<")

        leader_start = self.login(self.leader).get(reverse("vault:dashboard"))
        self.assertRedirects(leader_start, reverse("vault:card_list"), fetch_redirect_response=False)
        leader_vault = self.login(self.leader).get(reverse("vault:card_list"))
        self.assertContains(leader_vault, "Bóveda")
        self.assertNotContains(leader_vault, "Resumen operativo")

        analyst_start = self.login(self.analyst).get(reverse("vault:dashboard"))
        self.assertRedirects(analyst_start, reverse("vault:card_list"), fetch_redirect_response=False)
        analyst_vault = self.login(self.analyst).get(reverse("vault:card_list"))
        self.assertContains(analyst_vault, "Bóveda")
        self.assertNotContains(analyst_vault, "Resumen personal")

    def test_leader_cannot_access_administrative_control_center(self):
        self.assertEqual(self.login(self.leader).get(reverse("vault:control_center")).status_code, 403)

    def test_leader_and_analyst_cannot_access_timeline(self):
        self.assertEqual(self.login(self.analyst).get(reverse("vault:timeline")).status_code, 403)
        self.assertEqual(self.login(self.leader).get(reverse("vault:timeline")).status_code, 403)

    def test_timeline_paginates(self):
        for index in range(55): audit(None, "ACCESS", user=self.admin, metadata={"index": index})
        response = self.login(self.admin).get(reverse("vault:timeline"))
        self.assertEqual(response.context["page"].paginator.per_page, 50); self.assertTrue(response.context["page"].has_next())

    def test_policy_change_requires_reauthentication(self):
        client = self.login(self.admin)
        SensitiveOperationWindow.objects.filter(user=self.admin).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        response = client.post(reverse("vault:policy_settings"), {})
        self.assertIn("purpose=policy_admin", response.url)

    def test_unauthorized_user_cannot_modify_recipients(self):
        response = self.login(self.analyst).post(reverse("vault:recipients"), {})
        self.assertEqual(response.status_code, 403)

    def test_alert_close_requires_comment_and_transition_is_audited(self):
        event = audit(None, "ACCESS", user=self.leader)
        alert = SecurityAlert.objects.create(event=event, affected_user=self.leader, alert_type="TEST")
        client = self.login(self.admin)
        request = RequestFactory().post(reverse("vault:alert_detail", args=[alert.pk]))
        request.user = self.admin
        request.session = client.session
        grant_reauthentication(request, "alerts_manage")
        self.assertEqual(client.post(reverse("vault:alert_detail", args=[alert.pk]), {"status": "CLOSED", "review_note": ""}).status_code, 400)
        response = client.post(reverse("vault:alert_detail", args=[alert.pk]), {"status": "CLOSED", "review_note": "Caso validado y cerrado"})
        self.assertEqual(response.status_code, 302); self.assertTrue(AlertTransition.objects.filter(alert=alert, to_status="CLOSED").exists()); self.assertTrue(AuditEvent.objects.filter(action="ALERT_CLOSED").exists())
