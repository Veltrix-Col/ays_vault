import time
import uuid
import re
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.template.loader import get_template
from django.urls import reverse
from django.utils import timezone
from django_otp.oath import TOTP
from django_otp.plugins.otp_totp.models import TOTPDevice

from .identity import role_home_name
from .models import (
    AuditEvent,
    PaymentCard,
    PendingSensitiveOperation,
    SecureSession,
    SecurityAlert,
    SensitiveOperationWindow,
    UserProfile,
)


PASSWORD = "LongPassword123!"


@override_settings(
    APP_ENV="development",
    FIELD_ENCRYPTION_KEY="MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
    FIELD_FINGERPRINT_KEY="test-only-fingerprint-key",
)
class PendingSensitiveOperationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.admin = User.objects.create_user("admin.operaciones", password=PASSWORD)
        cls.target = User.objects.create_user("usuario.objetivo", password=PASSWORD)
        cls.leader = User.objects.create_user("lider.operaciones", password=PASSWORD)
        for user, role in (
            (cls.admin, UserProfile.ADMIN),
            (cls.target, UserProfile.ANALYST),
            (cls.leader, UserProfile.LEADER),
        ):
            profile = user.vault_profile
            profile.role = role
            profile.active = True
            profile.mfa_enabled = True
            profile.mfa_status = UserProfile.MFA_ACTIVE
            profile.save()
            TOTPDevice.objects.create(user=user, name="Prueba", confirmed=True)
        cls.card = PaymentCard(
            company_name="Empresa de prueba",
            client_name="Cliente de prueba",
            cardholder_name="Titular de prueba",
            brand="MASTERCARD",
            purpose="Prueba de desactivación",
            created_by=cls.leader,
            updated_by=cls.leader,
        )
        cls.card.set_pan("5555555555554444")
        cls.card.set_expiry("12/29")
        cls.card.set_code("CODIGO-DE-PRUEBA")
        cls.card.save()

    def token(self, user):
        device = TOTPDevice.objects.get(user=user, confirmed=True)
        device.last_t = -1
        device.throttling_failure_timestamp = None
        device.throttling_failure_count = 0
        device.save()
        totp = TOTP(device.bin_key, device.step, device.t0, device.digits, device.drift)
        totp.time = time.time()
        return str(totp.token()).zfill(device.digits)

    def login_admin(self):
        self.login_user(self.admin)

    def login_user(self, user):
        response = self.client.post(reverse("login"), {"username": user.username, "password": PASSWORD})
        self.assertRedirects(response, reverse("mfa_verify"), fetch_redirect_response=False)
        response = self.client.post(reverse("mfa_verify"), {"token": self.token(user), "recovery_code": ""})
        self.assertRedirects(response, reverse(role_home_name(user)), fetch_redirect_response=False)

    def expire_sensitive_window(self, user):
        SensitiveOperationWindow.objects.filter(
            user=user,
            revoked_at__isnull=True,
        ).update(expires_at=timezone.now() - timedelta(seconds=1))

    def begin_reset(self, operation_id=None):
        operation_id = operation_id or uuid.uuid4()
        response = self.client.post(
            reverse("vault:admin_mfa_reset", args=[self.target.pk]),
            {"reason": "Solicitud verificada por soporte", "operation_id": str(operation_id)},
        )
        self.assertEqual(response.status_code, 302)
        query = parse_qs(urlparse(response.url).query)
        self.assertEqual(query["purpose"], ["identity_admin"])
        self.assertEqual(query["operation"], [str(operation_id)])
        return operation_id, response

    def test_login_opens_fixed_30_minute_window_bound_to_same_session(self):
        self.login_user(self.leader)
        recovery_page = self.client.get(reverse("recovery_codes_confirm"))
        self.assertContains(
            recovery_page,
            "/static/img/branding/cardmanager/Logo-CardManager-CO-BLANCO.png",
        )
        self.assertNotContains(
            recovery_page,
            "/static/img/branding/logo-ays-azul.png",
        )
        session_key = self.client.session.session_key
        window = SensitiveOperationWindow.objects.get(
            user=self.leader,
            revoked_at__isnull=True,
        )
        lifetime = (window.expires_at - window.created_at).total_seconds()
        self.assertGreaterEqual(lifetime, 1799)
        self.assertLessEqual(lifetime, 1801)

        window.expires_at = timezone.now() - timedelta(seconds=1)
        window.save(update_fields=["expires_at"])
        page = self.client.get(
            reverse("vault:reauthenticate"),
            {"purpose": "cards_manage", "next": reverse("vault:card_list")},
        )
        self.assertContains(page, 'name="password"', count=1)
        self.assertNotContains(page, 'name="token"')
        self.assertContains(
            page,
            "/static/img/branding/cardmanager/Logo-CardManager-CO-BLANCO.png",
        )
        self.assertNotContains(page, "/static/img/branding/logo-ays-azul.png")
        response = self.client.post(
            reverse("vault:reauthenticate"),
            {
                "purpose": "cards_manage",
                "next": reverse("vault:card_list"),
                "password": PASSWORD,
            },
        )
        self.assertRedirects(
            response,
            reverse("vault:card_list"),
            fetch_redirect_response=False,
        )
        self.assertEqual(self.client.session.session_key, session_key)
        renewed = SensitiveOperationWindow.objects.get(
            user=self.leader,
            revoked_at__isnull=True,
        )
        renewed_lifetime = (renewed.expires_at - renewed.created_at).total_seconds()
        self.assertGreaterEqual(renewed_lifetime, 1799)
        self.assertLessEqual(renewed_lifetime, 1801)

    def complete_reset(self, operation_id):
        return self.client.post(
            reverse("vault:reauthenticate"),
            {
                "purpose": "identity_admin",
                "operation": str(operation_id),
                "next": reverse("vault:dashboard"),
                "password": PASSWORD,
            },
        )

    def test_mfa_reset_preserves_reason_and_executes_after_one_reauthentication(self):
        self.login_admin()
        self.expire_sensitive_window(self.admin)
        page = self.client.get(reverse("vault:admin_mfa_reset", args=[self.target.pk]))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'name="reason"', count=1)
        self.assertContains(page, 'name="operation_id"', count=1)

        operation_id, response = self.begin_reset()
        self.assertFalse(AuditEvent.objects.filter(action="MFA_RESET").exists())
        self.assertFalse(SecurityAlert.objects.filter(alert_type="MFA_RESET").exists())

        reauth = self.client.get(response.url)
        self.assertEqual(reauth.status_code, 200)
        self.assertNotContains(reauth, 'name="reason"')
        self.assertContains(reauth, 'name="password"', count=1)
        self.assertNotContains(reauth, 'name="token"')

        completed = self.complete_reset(operation_id)
        self.assertRedirects(completed, reverse("vault:identity_users"), fetch_redirect_response=False)
        operation = PendingSensitiveOperation.objects.get(public_id=operation_id)
        self.assertEqual(operation.reason, "Solicitud verificada por soporte")
        self.assertEqual(operation.status, PendingSensitiveOperation.COMPLETED)
        self.assertIsNotNone(operation.consumed_at)
        self.assertEqual(AuditEvent.objects.filter(action="MFA_RESET").count(), 1)
        self.assertEqual(SecurityAlert.objects.filter(alert_type="MFA_RESET").count(), 1)
        event = AuditEvent.objects.get(action="MFA_RESET")
        self.assertEqual(event.reason, operation.reason)
        self.assertEqual(event.metadata["operation_id"], str(operation_id))
        self.target.vault_profile.refresh_from_db()
        self.assertEqual(self.target.vault_profile.mfa_status, UserProfile.MFA_PENDING)
        self.assertFalse(TOTPDevice.objects.filter(user=self.target).exists())
        self.assertFalse(SecureSession.objects.filter(user=self.target, status=SecureSession.ACTIVE).exists())

    def test_repeated_initial_post_and_callback_do_not_execute_twice(self):
        self.login_admin()
        self.expire_sensitive_window(self.admin)
        operation_id, _ = self.begin_reset()
        self.complete_reset(operation_id)

        repeated = self.client.post(
            reverse("vault:admin_mfa_reset", args=[self.target.pk]),
            {"reason": "Solicitud verificada por soporte", "operation_id": str(operation_id)},
        )
        self.assertRedirects(repeated, reverse("vault:identity_users"), fetch_redirect_response=False)
        callback = self.client.get(
            reverse("vault:reauthenticate"),
            {"purpose": "identity_admin", "operation": str(operation_id)},
        )
        self.assertRedirects(callback, reverse("vault:identity_users"), fetch_redirect_response=False)
        repeated_callback = self.client.post(
            reverse("vault:reauthenticate"),
            {
                "purpose": "identity_admin",
                "operation": str(operation_id),
                "password": PASSWORD,
            },
        )
        self.assertRedirects(repeated_callback, reverse("vault:identity_users"), fetch_redirect_response=False)
        self.assertEqual(AuditEvent.objects.filter(action="MFA_RESET").count(), 1)
        self.assertEqual(SecurityAlert.objects.filter(alert_type="MFA_RESET").count(), 1)

    def test_pending_operation_is_bound_to_purpose_user_session_and_expiry(self):
        self.login_admin()
        self.expire_sensitive_window(self.admin)
        operation_id, _ = self.begin_reset()
        wrong_purpose = self.client.get(
            reverse("vault:reauthenticate"),
            {"purpose": "alerts_manage", "operation": str(operation_id)},
        )
        self.assertEqual(wrong_purpose.status_code, 302)
        self.assertFalse(AuditEvent.objects.filter(action="MFA_RESET").exists())

        operation = PendingSensitiveOperation.objects.get(public_id=operation_id)
        operation.expires_at = timezone.now() - timedelta(seconds=1)
        operation.save(update_fields=["expires_at"])
        expired = self.client.get(
            reverse("vault:reauthenticate"),
            {"purpose": "identity_admin", "operation": str(operation_id)},
        )
        self.assertRedirects(expired, reverse("vault:identity_users"), fetch_redirect_response=False)
        self.assertFalse(AuditEvent.objects.filter(action="MFA_RESET").exists())

        self.client.logout()
        other = get_user_model().objects.create_user("otro.admin", password=PASSWORD)
        profile = other.vault_profile
        profile.role = UserProfile.ADMIN
        profile.active = True
        profile.mfa_enabled = True
        profile.mfa_status = UserProfile.MFA_ACTIVE
        profile.save()
        TOTPDevice.objects.create(user=other, name="Prueba", confirmed=True)
        self.client.post(reverse("login"), {"username": other.username, "password": PASSWORD})
        self.client.post(reverse("mfa_verify"), {"token": self.token(other), "recovery_code": ""})
        foreign = self.client.get(
            reverse("vault:reauthenticate"),
            {"purpose": "identity_admin", "operation": str(operation_id)},
        )
        self.assertEqual(foreign.status_code, 302)
        self.assertFalse(AuditEvent.objects.filter(action="MFA_RESET").exists())

    def test_card_deactivation_resumes_after_reauthentication_and_is_idempotent(self):
        self.login_user(self.leader)
        self.expire_sensitive_window(self.leader)
        detail = self.client.get(reverse("vault:card_detail", args=[self.card.pk]))
        operation_id = re.search(
            r'name="operation_id" value="([^"]+)"',
            detail.content.decode(),
        ).group(1)
        start = self.client.post(
            reverse("vault:card_deactivate", args=[self.card.pk]),
            {"operation_id": operation_id},
        )
        self.assertEqual(start.status_code, 302)
        self.assertIn("purpose=cards_manage", start.url)
        self.card.refresh_from_db()
        self.assertTrue(self.card.active)

        finish = self.client.post(
            reverse("vault:reauthenticate"),
            {
                "purpose": "cards_manage",
                "operation": operation_id,
                "password": PASSWORD,
            },
        )
        self.assertRedirects(finish, reverse("vault:card_list"), fetch_redirect_response=False)
        self.card.refresh_from_db()
        self.assertFalse(self.card.active)
        self.assertEqual(
            AuditEvent.objects.filter(
                action="DEACTIVATE",
                metadata__operation_id=operation_id,
            ).count(),
            1,
        )

        repeated = self.client.post(
            reverse("vault:card_deactivate", args=[self.card.pk]),
            {"operation_id": operation_id},
        )
        self.assertRedirects(repeated, reverse("vault:card_list"), fetch_redirect_response=False)
        self.assertEqual(
            AuditEvent.objects.filter(
                action="DEACTIVATE",
                metadata__operation_id=operation_id,
            ).count(),
            1,
        )

    def test_card_and_protected_templates_compile_without_legacy_operation_tag(self):
        from .templatetags.vault_ui import register

        self.assertNotIn("sensitive_operation_id", register.tags)
        for template_name in (
            "vault/card_list.html",
            "vault/_card_results.html",
            "vault/card_detail.html",
            "vault/card_form.html",
            "vault/security/_protected_identity.html",
            "vault/security/_operation_context.html",
            "vault/security/reauthenticate.html",
            "vault/security/admin_mfa_reset.html",
        ):
            self.assertIsNotNone(get_template(template_name), template_name)

        self.login_user(self.leader)
        response = self.client.get(reverse("vault:card_detail", args=[self.card.pk]))
        self.assertEqual(response.status_code, 200)
        operation_id = re.search(
            r'name="operation_id" value="([^"]+)"',
            response.content.decode(),
        ).group(1)
        self.assertEqual(str(uuid.UUID(operation_id)), operation_id)
