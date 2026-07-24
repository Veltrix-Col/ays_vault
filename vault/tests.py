import time
from datetime import timedelta

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django_otp.oath import TOTP
from django_otp.plugins.otp_totp.models import TOTPDevice

from .forms import CardForm, luhn_valid
from .identity import generate_recovery_codes, grant_reauthentication
from .crypto import encrypt
from .models import AuditEvent, MFARecoveryCode, PaymentCard, ProtectedOperationContext, ReauthenticationGrant, RevealGrant, SecureSession, SecurityAlert, SensitiveOperationWindow, UserDevice, UserProfile
from .identity import role_home_name
from .security import session_hash, verify_audit_chain


PASSWORD = "LongPassword123!"
PAN = "4111111111111111"


@override_settings(APP_ENV="development", FIELD_ENCRYPTION_KEY="MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=", FIELD_FINGERPRINT_KEY="test-only-fingerprint-key", MFA_FAILURE_LIMIT=3)
class VaultIdentitySecurityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.admin_user = User.objects.create_user("admin.persona", password=PASSWORD)
        cls.leader = User.objects.create_user("lider.persona", password=PASSWORD)
        cls.analyst = User.objects.create_user("analista.persona", password=PASSWORD)
        cls.unassigned = User.objects.create_user("pendiente.persona", password=PASSWORD)
        for user, role in ((cls.admin_user, UserProfile.ADMIN), (cls.leader, UserProfile.LEADER), (cls.analyst, UserProfile.ANALYST)):
            profile = user.vault_profile; profile.role = role; profile.active = True; profile.mfa_enabled = True; profile.mfa_status = UserProfile.MFA_ACTIVE; profile.save()
            TOTPDevice.objects.create(user=user, name="Test", confirmed=True)
        cls.card = PaymentCard(client_name="Cliente Demo", cardholder_name="Titular Demo", brand="VISA", purpose="Prueba autorizada", created_by=cls.leader, updated_by=cls.leader)
        cls.card.set_pan(PAN); cls.card.set_expiry("12/29"); cls.card.set_company("Empresa Protegida Demo"); cls.card.save()

    def token(self, user, reset_last_t=False):
        device = TOTPDevice.objects.filter(user=user, confirmed=True).first()
        if reset_last_t:
            device.last_t = -1; device.throttling_failure_timestamp = None; device.throttling_failure_count = 0; device.save()
        totp = TOTP(device.bin_key, device.step, device.t0, device.digits, device.drift)
        totp.time = time.time()
        return str(totp.token()).zfill(device.digits)

    def login_mfa(self, user, client=None):
        client = client or self.client
        response = client.post(reverse("login"), {"username": user.username, "password": PASSWORD})
        self.assertRedirects(response, reverse("mfa_verify"), fetch_redirect_response=False)
        response = client.post(reverse("mfa_verify"), {"token": self.token(user, reset_last_t=True), "recovery_code": ""})
        self.assertRedirects(response, reverse(role_home_name(user)), fetch_redirect_response=False)
        return client

    def grant(self, user, purpose, client=None):
        client = client or self.client
        self.login_mfa(user, client)
        session = client.session
        ReauthenticationGrant.objects.create(user=user, session_hash=session_hash(session.session_key), purpose=purpose, expires_at=timezone.now() + timedelta(minutes=5))
        return client

    def operation_window(self, user, client=None, reason="Pago autorizado", reference="POL-123456"):
        client = client or self.client
        self.login_mfa(user, client)
        identifier = session_hash(client.session.session_key)
        window = SensitiveOperationWindow.objects.create(
            user=user, session_hash=identifier, expires_at=timezone.now() + timedelta(minutes=15),
        )
        ProtectedOperationContext.objects.create(
            identity_window=window, user=user, session_hash=identifier, card=self.card,
            reason=reason, internal_reference=reference, expires_at=window.expires_at,
        )
        return client, window

    def test_password_alone_does_not_authenticate(self):
        response = self.client.post(reverse("login"), {"username": self.analyst.username, "password": PASSWORD})
        self.assertRedirects(response, reverse("mfa_verify"), fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertTrue(AuditEvent.objects.filter(action="MFA_REQUIRED", user=self.analyst).exists())

    def test_user_without_mfa_is_forced_to_enroll(self):
        self.analyst.vault_profile.mfa_status = UserProfile.MFA_PENDING; self.analyst.vault_profile.mfa_enabled = False; self.analyst.vault_profile.save()
        TOTPDevice.objects.filter(user=self.analyst).delete()
        response = self.client.post(reverse("login"), {"username": self.analyst.username, "password": PASSWORD})
        self.assertRedirects(response, reverse("mfa_enroll"), fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_wrong_otp_blocks_access_and_is_audited_without_token(self):
        self.client.post(reverse("login"), {"username": self.analyst.username, "password": PASSWORD})
        response = self.client.post(reverse("mfa_verify"), {"token": "000000", "recovery_code": ""})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)
        event = AuditEvent.objects.filter(action="MFA_FAILED").latest("sequence")
        self.assertNotIn("000000", str(event.metadata) + event.reason)

    def test_correct_otp_creates_verified_secure_session(self):
        self.login_mfa(self.analyst)
        record = SecureSession.objects.get(user=self.analyst, status=SecureSession.ACTIVE)
        self.assertTrue(record.mfa_completed)
        self.assertNotEqual(record.encrypted_session_key, self.client.session.session_key)
        self.assertEqual(self.client.get(reverse("vault:card_list")).status_code, 200)

    def test_incomplete_enrollment_does_not_activate_mfa(self):
        self.analyst.vault_profile.mfa_status = UserProfile.MFA_PENDING; self.analyst.vault_profile.mfa_enabled = False; self.analyst.vault_profile.save()
        TOTPDevice.objects.filter(user=self.analyst).delete()
        self.client.post(reverse("login"), {"username": self.analyst.username, "password": PASSWORD})
        self.client.get(reverse("mfa_enroll"))
        self.assertFalse(TOTPDevice.objects.get(user=self.analyst).confirmed)
        self.analyst.vault_profile.refresh_from_db(); self.assertFalse(self.analyst.vault_profile.mfa_enabled)

    def test_enrollment_activates_only_after_password_and_totp(self):
        self.analyst.vault_profile.mfa_status = UserProfile.MFA_PENDING; self.analyst.vault_profile.mfa_enabled = False; self.analyst.vault_profile.save()
        TOTPDevice.objects.filter(user=self.analyst).delete()
        self.client.post(reverse("login"), {"username": self.analyst.username, "password": PASSWORD})
        self.client.get(reverse("mfa_enroll")); device = TOTPDevice.objects.get(user=self.analyst)
        totp = TOTP(device.bin_key, device.step, device.t0, device.digits, device.drift); totp.time = time.time()
        response = self.client.post(reverse("mfa_enroll"), {"password": PASSWORD, "token": str(totp.token()).zfill(6)})
        self.assertEqual(response.status_code, 200)
        device.refresh_from_db(); self.assertTrue(device.confirmed)
        self.analyst.vault_profile.refresh_from_db(); self.assertEqual(self.analyst.vault_profile.mfa_status, UserProfile.MFA_ACTIVE)
        self.assertEqual(MFARecoveryCode.objects.filter(user=self.analyst).count(), 10)

    def test_totp_device_is_not_registered_in_admin(self):
        self.assertFalse(admin.site.is_registered(TOTPDevice))

    def test_recovery_codes_are_hashed_and_single_use(self):
        values = generate_recovery_codes(self.analyst)
        stored = MFARecoveryCode.objects.filter(user=self.analyst).first()
        self.assertNotEqual(stored.code_hash, values[0]); self.assertTrue(check_password(values[0], stored.code_hash))
        self.client.post(reverse("login"), {"username": self.analyst.username, "password": PASSWORD})
        response = self.client.post(reverse("mfa_verify"), {"token": "", "recovery_code": values[0]})
        self.assertEqual(response.status_code, 302)
        stored.refresh_from_db(); self.assertIsNotNone(stored.used_at)
        self.client.post(reverse("logout")); self.client.post(reverse("login"), {"username": self.analyst.username, "password": PASSWORD})
        self.assertEqual(self.client.post(reverse("mfa_verify"), {"token": "", "recovery_code": values[0]}).status_code, 200)

    def test_regeneration_invalidates_previous_codes(self):
        old = generate_recovery_codes(self.analyst); new = generate_recovery_codes(self.analyst)
        self.assertFalse(any(check_password(old[0], row.code_hash) for row in MFARecoveryCode.objects.filter(user=self.analyst)))
        self.assertTrue(any(check_password(new[0], row.code_hash) for row in MFARecoveryCode.objects.filter(user=self.analyst)))

    def test_second_session_revokes_first_django_session(self):
        first = self.login_mfa(self.analyst, Client())
        first_key = first.session.session_key
        second = self.login_mfa(self.analyst, Client())
        self.assertFalse(SecureSession.objects.filter(user=self.analyst, status=SecureSession.ACTIVE, encrypted_session_key__isnull=False).count() > 1)
        self.assertNotEqual(first.get(reverse("vault:dashboard")).status_code, 200)
        self.assertTrue(AuditEvent.objects.filter(action="SESSION_REPLACED", user=self.analyst).exists())

    def test_inactivity_expires_session_and_authorizations(self):
        self.login_mfa(self.analyst)
        record = SecureSession.objects.get(user=self.analyst, status=SecureSession.ACTIVE)
        window = SensitiveOperationWindow.objects.create(user=self.analyst, session_hash=record.session_hash, expires_at=timezone.now() + timedelta(minutes=15))
        ProtectedOperationContext.objects.create(identity_window=window, user=self.analyst, session_hash=record.session_hash, card=self.card, reason="Pago", internal_reference="REF-1", expires_at=window.expires_at)
        record.last_activity_at = timezone.now() - timedelta(minutes=11); record.save(update_fields=["last_activity_at"])
        response = self.client.get(reverse("vault:dashboard"))
        self.assertRedirects(response, reverse("login"), fetch_redirect_response=False)
        record.refresh_from_db(); self.assertEqual(record.status, SecureSession.EXPIRED)
        self.assertFalse(SensitiveOperationWindow.objects.filter(user=self.analyst, revoked_at__isnull=True).exists())
        self.assertFalse(ProtectedOperationContext.objects.filter(user=self.analyst, closed_at__isnull=True).exists())

    def test_logout_invalidates_reveal_and_reauthentication(self):
        self.login_mfa(self.analyst); record = SecureSession.objects.get(user=self.analyst, status=SecureSession.ACTIVE)
        window = SensitiveOperationWindow.objects.create(user=self.analyst, session_hash=record.session_hash, expires_at=timezone.now() + timedelta(minutes=15))
        context = ProtectedOperationContext.objects.create(identity_window=window, user=self.analyst, session_hash=record.session_hash, card=self.card, reason="Prueba", internal_reference="REF-1", expires_at=window.expires_at)
        RevealGrant.objects.create(token_hash="a" * 64, user=self.analyst, card=self.card, field_name="pan", session_key=record.session_hash, operation_window=window, operation_context=context, expires_at=timezone.now() + timedelta(seconds=20))
        self.client.post(reverse("logout"))
        self.assertFalse(ReauthenticationGrant.objects.filter(user=self.analyst, invalidated_at__isnull=True).exists())
        self.assertFalse(RevealGrant.objects.filter(user=self.analyst).exists())
        window.refresh_from_db(); self.assertIsNotNone(window.revoked_at)

    def test_reveal_requires_recent_purpose_bound_reauthentication(self):
        self.login_mfa(self.analyst)
        url = reverse("vault:reveal", args=[self.card.pk])
        data = {"field": "pan", "action": "reveal"}
        self.assertEqual(self.client.post(url, data).status_code, 428)
        record = SecureSession.objects.get(user=self.analyst, status=SecureSession.ACTIVE)
        ReauthenticationGrant.objects.create(user=self.analyst, session_hash=record.session_hash, purpose="cards_manage", expires_at=timezone.now() + timedelta(minutes=5))
        self.assertEqual(self.client.post(url, data).status_code, 428)
        window = SensitiveOperationWindow.objects.create(user=self.analyst, session_hash=record.session_hash, expires_at=timezone.now() + timedelta(minutes=15))
        ProtectedOperationContext.objects.create(identity_window=window, user=self.analyst, session_hash=record.session_hash, card=self.card, reason="Pago prueba", internal_reference="FAC-1", expires_at=window.expires_at)
        response = self.client.post(url, data); self.assertEqual(response.status_code, 200); self.assertEqual(response.content.decode(), PAN)

    def test_actual_reauthentication_requires_password_and_totp(self):
        self.login_mfa(self.analyst)
        response = self.client.post(reverse("vault:reauthenticate"), {"purpose": "reveal", "next": reverse("vault:dashboard"), "password": PASSWORD, "token": self.token(self.analyst, reset_last_t=True)})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(ReauthenticationGrant.objects.filter(user=self.analyst, purpose="reveal").exists())

    def test_expired_and_other_session_reauthentication_do_not_apply(self):
        self.login_mfa(self.analyst); record = SecureSession.objects.get(user=self.analyst, status=SecureSession.ACTIVE)
        SensitiveOperationWindow.objects.create(user=self.analyst, session_hash=record.session_hash, expires_at=timezone.now() - timedelta(seconds=1))
        SensitiveOperationWindow.objects.create(user=self.analyst, session_hash="f" * 64, expires_at=timezone.now() + timedelta(minutes=15))
        response = self.client.post(reverse("vault:reveal", args=[self.card.pk]), {"field": "pan", "action": "reveal"})
        self.assertEqual(response.status_code, 428)

    def test_copy_requires_one_time_reveal_grant_and_never_audits_pan(self):
        self.operation_window(self.analyst)
        reveal = self.client.post(reverse("vault:reveal", args=[self.card.pk]), {"field": "pan", "action": "copy"})
        url = reverse("vault:copy_event", args=[self.card.pk])
        token = reveal.headers["X-Vault-Copy-Token"]
        self.assertEqual(self.client.post(url, {"copy_token": token, "result": "success"}).status_code, 200)
        self.assertEqual(self.client.post(url, {"copy_token": token, "result": "success"}).status_code, 409)
        event = AuditEvent.objects.filter(action="COPY").latest("sequence")
        self.assertNotIn(PAN, event.reason + str(event.metadata))

    def test_admin_mfa_reset_revokes_sessions_devices_and_old_totp(self):
        target_client = self.login_mfa(self.analyst, Client())
        target_device = UserDevice.objects.get(user=self.analyst); target_device.status = UserDevice.TRUSTED; target_device.trusted_until = timezone.now() + timedelta(days=30); target_device.save()
        generate_recovery_codes(self.analyst)
        self.login_mfa(self.admin_user)
        admin_record = SecureSession.objects.get(user=self.admin_user, status=SecureSession.ACTIVE)
        ReauthenticationGrant.objects.create(user=self.admin_user, session_hash=admin_record.session_hash, purpose="identity_admin", expires_at=timezone.now() + timedelta(minutes=5))
        response = self.client.post(reverse("vault:admin_mfa_reset", args=[self.analyst.pk]), {"reason": "Recuperación verificada"})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(TOTPDevice.objects.filter(user=self.analyst).exists())
        self.assertFalse(MFARecoveryCode.objects.filter(user=self.analyst).exists())
        self.assertFalse(SecureSession.objects.filter(user=self.analyst, status=SecureSession.ACTIVE).exists())
        target_device.refresh_from_db(); self.assertEqual(target_device.status, UserDevice.NEW)
        self.analyst.vault_profile.refresh_from_db(); self.assertEqual(self.analyst.vault_profile.mfa_status, UserProfile.MFA_PENDING)
        self.assertNotEqual(target_client.get(reverse("vault:dashboard")).status_code, 200)

    def test_analyst_cannot_view_or_revoke_another_users_sessions(self):
        self.login_mfa(self.analyst)
        self.assertEqual(self.client.get(reverse("vault:admin_sessions", args=[self.leader.pk])).status_code, 403)

    def test_password_change_revokes_other_sessions_and_device_trust(self):
        self.login_mfa(self.analyst); current = SecureSession.objects.get(user=self.analyst, status=SecureSession.ACTIVE)
        current.device.status = UserDevice.TRUSTED; current.device.trusted_until = timezone.now() + timedelta(days=30); current.device.save()
        other = SecureSession.objects.create(user=self.analyst, session_hash="e" * 64, encrypted_session_key=encrypt("fake-session-key"), last_activity_at=timezone.now(), expires_at=timezone.now() + timedelta(minutes=10))
        ReauthenticationGrant.objects.create(user=self.analyst, session_hash=current.session_hash, purpose="password_change", expires_at=timezone.now() + timedelta(minutes=5))
        response = self.client.post(reverse("vault:password_change"), {"old_password": PASSWORD, "new_password1": "DifferentPassword456!", "new_password2": "DifferentPassword456!"})
        self.assertEqual(response.status_code, 302)
        other.refresh_from_db(); self.assertEqual(other.status, SecureSession.REVOKED)
        current.device.refresh_from_db(); self.assertEqual(current.device.status, UserDevice.NEW)

    def test_critical_post_requires_csrf(self):
        self.login_mfa(self.analyst)
        client = Client(enforce_csrf_checks=True); client.cookies = self.client.cookies
        self.assertEqual(client.post(reverse("vault:reveal", args=[self.card.pk]), {"field": "pan", "action": "reveal"}).status_code, 403)

    def test_new_device_creates_alert(self):
        self.login_mfa(self.analyst)
        self.assertTrue(UserDevice.objects.filter(user=self.analyst, status=UserDevice.NEW).exists())
        self.assertTrue(SecurityAlert.objects.filter(affected_user=self.analyst, alert_type="NEW_DEVICE").exists())

    def test_blocked_device_cannot_complete_login(self):
        self.client.defaults["HTTP_USER_AGENT"] = "Blocked Browser"
        self.client.post(reverse("login"), {"username": self.analyst.username, "password": PASSWORD})
        device = UserDevice.objects.get(user=self.analyst); device.status = UserDevice.BLOCKED; device.save(update_fields=["status"])
        self.client.get(reverse("login")); response = self.client.post(reverse("login"), {"username": self.analyst.username, "password": PASSWORD})
        self.assertEqual(response.status_code, 200); self.assertNotIn("_auth_user_id", self.client.session)

    def test_trusted_device_still_requires_mfa(self):
        self.client.defaults["HTTP_USER_AGENT"] = "Trusted Browser"
        self.client.post(reverse("login"), {"username": self.analyst.username, "password": PASSWORD})
        device = UserDevice.objects.get(user=self.analyst); device.status = UserDevice.TRUSTED; device.trusted_until = timezone.now() + timedelta(days=1); device.save()
        response = self.client.post(reverse("login"), {"username": self.analyst.username, "password": PASSWORD})
        self.assertRedirects(response, reverse("mfa_verify"), fetch_redirect_response=False)

    def test_role_matrix_remains_backend_enforced(self):
        url = reverse("vault:card_list")
        for user, expected in ((self.admin_user, 403), (self.leader, 200), (self.analyst, 200)):
            client = self.login_mfa(user, Client()); self.assertEqual(client.get(url).status_code, expected)

    def test_analyst_cannot_access_inactive_card_idor(self):
        self.card.active = False; self.card.save(update_fields=["active"])
        self.login_mfa(self.analyst)
        self.assertEqual(self.client.get(reverse("vault:card_detail", args=[self.card.pk])).status_code, 404)

    def test_admin_cannot_access_sensitive_card_endpoints(self):
        self.login_mfa(self.admin_user)
        self.assertEqual(self.client.get(reverse("vault:card_detail", args=[self.card.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse("vault:copy_event", args=[self.card.pk]), {"copy_token": "x"}).status_code, 403)

    def test_pan_expiry_encrypted_and_duplicate_rejected(self):
        stored = PaymentCard.objects.get(pk=self.card.pk)
        self.assertNotIn(PAN, stored.encrypted_pan); self.assertNotIn("12/29", stored.encrypted_expiry); self.assertTrue(luhn_valid(PAN))
        form = CardForm(data={"client_name": "Otro", "cardholder_name": "Demo", "brand": "VISA", "purpose": "Demo", "active": True, "pan": PAN, "expiry": "10/30", "company": "Empresa Demo"})
        self.assertFalse(form.is_valid())

    def test_session_records_contain_no_plain_session_key(self):
        self.login_mfa(self.analyst); record = SecureSession.objects.get(user=self.analyst, status=SecureSession.ACTIVE)
        self.assertNotEqual(record.session_hash, self.client.session.session_key)
        self.assertNotIn(self.client.session.session_key, str(record.__dict__))

    def test_audit_chain_detects_tampering_with_new_events(self):
        self.login_mfa(self.analyst)
        valid, _ = verify_audit_chain(); self.assertTrue(valid)
        event = AuditEvent.objects.latest("sequence"); AuditEvent.objects.filter(pk=event.pk).update(reason="alterado")
        valid, position = verify_audit_chain(); self.assertFalse(valid); self.assertEqual(position, event.sequence)
