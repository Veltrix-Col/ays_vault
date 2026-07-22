import time
from datetime import timedelta
from io import BytesIO

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django_otp.oath import TOTP
from django_otp.plugins.otp_totp.models import TOTPDevice
from openpyxl import load_workbook

from .crypto import encrypt
from .models import (
    AuditEvent,
    NotificationRecipient,
    PaymentCard,
    PolicyConfiguration,
    ProtectedOperationContext,
    ReauthenticationGrant,
    SecureSession,
    SensitiveOperationWindow,
    UserProfile,
)
from .security import session_hash, verify_audit_chain
from .policies import invalidate_policy_cache


PASSWORD = "IntegralSecure123!"
COMPANY = "Empresa Ultrasecreta S.A.S."
PAN = "4111111111111111"


@override_settings(
    APP_ENV="development",
    FIELD_ENCRYPTION_KEY="MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
    FIELD_FINGERPRINT_KEY="integral-test-key",
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
    ALERT_EMAIL_BACKEND="console",
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    AXES_ENABLED=False,
)
class IntegralVaultFlowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.admin = User.objects.create_user("admin.integral", password=PASSWORD)
        cls.leader = User.objects.create_user("lider.integral", password=PASSWORD)
        cls.analyst = User.objects.create_user("analista.integral", password=PASSWORD)
        for user, role in ((cls.admin, UserProfile.ADMIN), (cls.leader, UserProfile.LEADER), (cls.analyst, UserProfile.ANALYST)):
            profile = user.vault_profile
            profile.role = role
            profile.active = profile.mfa_enabled = True
            profile.mfa_status = UserProfile.MFA_ACTIVE
            profile.save()
            TOTPDevice.objects.create(user=user, confirmed=True)
        cls.card = PaymentCard(
            client_name="Cliente Seguro",
            cardholder_name="Titular Autorizado",
            brand="VISA",
            purpose="Operación autorizada",
            created_by=cls.leader,
        )
        cls.card.set_pan(PAN)
        cls.card.set_expiry("12/29")
        cls.card.set_company(COMPANY)
        cls.card.save()
        cls.other_card = PaymentCard(
            client_name="Segundo Cliente",
            cardholder_name="Otra Persona",
            brand="MC",
            purpose="Segunda operación",
            created_by=cls.leader,
        )
        cls.other_card.set_pan("5555555555554444")
        cls.other_card.set_expiry("09/30")
        cls.other_card.set_company("Otra Empresa Protegida")
        cls.other_card.save()

    def setUp(self):
        PolicyConfiguration.objects.update_or_create(singleton=1, defaults={"outside_hours_behavior": "ALLOW"})
        invalidate_policy_cache()

    def authenticated_client(self, user):
        client = Client(REMOTE_ADDR="10.50.0.10", HTTP_USER_AGENT="Integral Browser")
        client.force_login(user)
        session = client.session
        device = TOTPDevice.objects.get(user=user)
        session["otp_device_id"] = device.persistent_id
        session.save()
        now = timezone.now()
        SecureSession.objects.update_or_create(
            user=user,
            session_hash=session_hash(session.session_key),
            defaults={
                "encrypted_session_key": encrypt(session.session_key),
                "last_activity_at": now,
                "expires_at": now + timedelta(minutes=10),
                "initial_ip": "10.50.0.10",
                "last_ip": "10.50.0.10",
                "user_agent": "Integral Browser",
                "status": SecureSession.ACTIVE,
                "mfa_completed": True,
                "mfa_completed_at": now,
            },
        )
        return client

    def token(self, user):
        device = TOTPDevice.objects.get(user=user)
        device.last_t = -1
        device.throttling_failure_timestamp = None
        device.throttling_failure_count = 0
        device.save()
        totp = TOTP(device.bin_key, device.step, device.t0, device.digits, device.drift)
        totp.time = time.time()
        return str(totp.token()).zfill(device.digits)

    def authorize_operation(self, client, user, card=None, field="company", action="reveal", reason="Pago autorizado de renovación", reference="POL-123456", expect_identity=True):
        card = card or self.card
        start = client.post(reverse("vault:reveal", args=[card.pk]), {"field": field, "action": action})
        self.assertEqual(start.status_code, 428)
        payload = start.json()
        intent = payload["intent"]
        self.assertEqual(payload["stage"], "identity" if expect_identity else "context")
        if expect_identity:
            self.assertIn("Ingrese su contraseña", payload["form_html"])
            identity = client.post(
                reverse("vault:protected_reauthenticate"),
                {"intent": intent, "password": PASSWORD, "token": self.token(user)},
            )
            self.assertEqual(identity.status_code, 200)
            self.assertContains(identity, "Indique el contexto de esta operación")
        else:
            self.assertIn("Referencia interna", payload["form_html"])
            self.assertNotIn("Ingrese su contraseña", payload["form_html"])
        confirm = client.post(
            reverse("vault:protected_confirm"),
            {"intent": intent, "reason": reason, "reference": reference},
        )
        self.assertEqual(confirm.status_code, 200)
        return (
            SensitiveOperationWindow.objects.get(user=user, revoked_at__isnull=True),
            ProtectedOperationContext.objects.get(user=user, closed_at__isnull=True),
        )

    def test_operational_roles_only_see_vault_and_admin_modules_are_backend_blocked(self):
        restricted = ["control_center", "timeline", "alerts", "report_center", "sessions", "devices", "policy_settings", "exceptions", "holidays", "recipients", "identity_users"]
        for user in (self.leader, self.analyst):
            client = self.authenticated_client(user)
            page = client.get(reverse("vault:card_list"))
            self.assertContains(page, ">Bóveda<", html=False)
            for label in ("Centro de Control", "Línea de tiempo", "Informes", "Sesiones", "Dispositivos", "Correo y destinatarios"):
                self.assertNotContains(page, label)
            for name in restricted:
                self.assertEqual(client.get(reverse(f"vault:{name}")).status_code, 403, name)

    def test_role_home_and_card_management_matrix(self):
        self.assertRedirects(self.authenticated_client(self.admin).get(reverse("vault:dashboard")), reverse("vault:control_center"), fetch_redirect_response=False)
        self.assertRedirects(self.authenticated_client(self.leader).get(reverse("vault:dashboard")), reverse("vault:card_list"), fetch_redirect_response=False)
        self.assertRedirects(self.authenticated_client(self.analyst).get(reverse("vault:dashboard")), reverse("vault:card_list"), fetch_redirect_response=False)
        self.assertEqual(self.authenticated_client(self.admin).get(reverse("vault:card_list")).status_code, 403)
        self.assertEqual(self.authenticated_client(self.analyst).get(reverse("vault:card_create")).status_code, 403)

    def test_server_search_uses_safe_fields_ajax_and_pagination(self):
        client = self.authenticated_client(self.analyst)
        url = reverse("vault:card_list")
        for query in (str(self.card.pk), "Cliente Seguro", "Titular Autorizado", "VISA", "1111"):
            response = client.get(url, {"q": query}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "Cliente Seguro")
            self.assertNotContains(response, COMPANY)
            self.assertNotContains(response, PAN)
        self.assertContains(client.get(url, {"q": "inexistente"}), "Sin resultados")
        rejected = client.get(url, {"q": PAN}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertNotContains(rejected, "Cliente Seguro")

    def test_company_is_encrypted_and_absent_from_initial_html_and_admin_exports(self):
        stored = PaymentCard.objects.get(pk=self.card.pk)
        self.assertNotIn(COMPANY, stored.encrypted_company)
        detail = self.authenticated_client(self.leader).get(reverse("vault:card_detail", args=[self.card.pk]))
        self.assertNotContains(detail, COMPANY)
        self.assertContains(detail, "Dato protegido")
        admin = self.authenticated_client(self.admin)
        export = admin.post(reverse("vault:export_report", args=["CARDS", "XLSX"]))
        workbook = load_workbook(BytesIO(export.content))
        text = " ".join(str(cell.value) for sheet in workbook for row in sheet for cell in row)
        self.assertNotIn(COMPANY, text)
        self.assertNotIn(PAN, text)

    def test_historical_card_without_company_is_supported(self):
        historical = PaymentCard(client_name="Histórica", cardholder_name="Titular", brand="VISA", purpose="Histórica", created_by=self.leader)
        historical.set_pan("4012888888881881")
        historical.set_expiry("11/30")
        historical.save()
        self.assertEqual(historical.get_company(), "")
        client = self.authenticated_client(self.leader)
        ReauthenticationGrant.objects.create(
            user=self.leader,
            session_hash=session_hash(client.session.session_key),
            purpose="cards_manage",
            expires_at=timezone.now() + timedelta(minutes=5),
        )
        response = client.get(reverse("vault:card_edit", args=[historical.pk]))
        self.assertContains(response, "Empresa actual:")
        self.assertContains(response, "no configurada")
        self.assertNotContains(response, COMPANY)

    def test_first_operation_creates_identity_window_and_card_context(self):
        client = self.authenticated_client(self.analyst)
        window, context = self.authorize_operation(client, self.analyst)
        lifetime = (window.expires_at - window.created_at).total_seconds()
        self.assertGreaterEqual(lifetime, 899)
        self.assertLessEqual(lifetime, 901)
        self.assertFalse(hasattr(window, "reason"))
        self.assertFalse(hasattr(window, "internal_reference"))
        self.assertEqual(context.card, self.card)
        self.assertEqual(context.reason, "Pago autorizado de renovación")
        self.assertEqual(context.internal_reference, "POL-123456")

    def test_three_fields_of_same_card_share_context_and_audit_individually(self):
        client = self.authenticated_client(self.analyst)
        window, context = self.authorize_operation(client, self.analyst)
        for field, value in (("company", COMPANY), ("pan", PAN), ("expiry", "12/29")):
            response = client.post(reverse("vault:reveal", args=[self.card.pk]), {"field": field, "action": "reveal"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.content.decode(), value)
            self.assertEqual(response.headers["X-Vault-Expires-In"], "20")
            self.assertIn("no-store", response.headers["Cache-Control"])
        events = AuditEvent.objects.filter(user=self.analyst, action="REVEAL")
        self.assertEqual(events.count(), 3)
        self.assertTrue(all(event.metadata["context_id"] == str(context.public_id) for event in events))
        self.assertTrue(all(event.metadata["window_id"] == str(window.public_id) for event in events))
        self.assertTrue(all(event.reason == context.reason for event in events))
        self.assertFalse(any(COMPANY in str(event.metadata) + event.reason for event in events))
        self.assertTrue(verify_audit_chain()[0])

    def test_new_card_inside_identity_window_requires_fresh_context_only(self):
        client = self.authenticated_client(self.analyst)
        window, first_context = self.authorize_operation(client, self.analyst, reason="Primera compra", reference="REF-PRIMERA")
        same_card = client.post(reverse("vault:reveal", args=[self.card.pk]), {"field": "pan", "action": "reveal"})
        self.assertEqual(same_card.status_code, 200)

        same_window, second_context = self.authorize_operation(
            client,
            self.analyst,
            card=self.other_card,
            reason="Segunda compra",
            reference="REF-SEGUNDA",
            expect_identity=False,
        )
        self.assertEqual(same_window.pk, window.pk)
        first_context.refresh_from_db()
        self.assertIsNotNone(first_context.closed_at)
        self.assertEqual(second_context.card, self.other_card)
        self.assertEqual(second_context.reason, "Segunda compra")
        self.assertEqual(second_context.internal_reference, "REF-SEGUNDA")
        self.assertNotEqual(second_context.reason, first_context.reason)
        response = client.post(reverse("vault:reveal", args=[self.other_card.pk]), {"field": "expiry", "action": "reveal"})
        self.assertEqual(response.status_code, 200)
        event = AuditEvent.objects.filter(action="REVEAL", card=self.other_card).latest("sequence")
        self.assertEqual(event.reason, "Segunda compra")
        self.assertEqual(event.metadata["reference"], "REF-SEGUNDA")
        self.assertNotIn("REF-PRIMERA", str(event.metadata))

    def test_returning_to_vault_starts_new_operation_without_repeating_identity(self):
        client = self.authenticated_client(self.analyst)
        window, first_context = self.authorize_operation(client, self.analyst, reason="Contexto inicial", reference="REF-1")
        client.get(reverse("vault:card_list"))
        first_context.refresh_from_db()
        self.assertIsNotNone(first_context.closed_at)
        same_window, second_context = self.authorize_operation(
            client, self.analyst, reason="Nueva operación", reference="REF-2", expect_identity=False,
        )
        self.assertEqual(same_window.pk, window.pk)
        self.assertEqual(second_context.reason, "Nueva operación")

    def test_identity_must_succeed_before_context_and_context_rejects_protected_values(self):
        client = self.authenticated_client(self.analyst)
        start = client.post(reverse("vault:reveal", args=[self.card.pk]), {"field": "company", "action": "reveal"})
        intent = start.json()["intent"]
        bad = client.post(reverse("vault:protected_reauthenticate"), {"intent": intent, "password": "incorrecta", "token": "000000"})
        self.assertEqual(bad.status_code, 400)
        self.assertNotContains(bad, "Referencia interna", status_code=400)
        premature = client.post(reverse("vault:protected_confirm"), {"intent": intent, "reason": "Pago autorizado", "reference": "POL-1"})
        self.assertEqual(premature.status_code, 400)
        ok = client.post(reverse("vault:protected_reauthenticate"), {"intent": intent, "password": PASSWORD, "token": self.token(self.analyst)})
        self.assertEqual(ok.status_code, 200)
        protected = client.post(reverse("vault:protected_confirm"), {"intent": intent, "reason": f"Pago {COMPANY}", "reference": "12/29"})
        self.assertEqual(protected.status_code, 400)
        self.assertTrue(SensitiveOperationWindow.objects.filter(user=self.analyst, revoked_at__isnull=True).exists())
        self.assertFalse(ProtectedOperationContext.objects.filter(user=self.analyst).exists())

    def test_copy_tokens_are_one_use_and_bound_to_window_session_and_user(self):
        client = self.authenticated_client(self.analyst)
        _, context = self.authorize_operation(client, self.analyst, action="copy")
        reveal = client.post(reverse("vault:reveal", args=[self.card.pk]), {"field": "company", "action": "copy"})
        token = reveal.headers["X-Vault-Copy-Token"]
        copy_url = reverse("vault:copy_event", args=[self.card.pk])
        self.assertEqual(client.post(copy_url, {"copy_token": token, "result": "success"}).status_code, 200)
        self.assertEqual(client.post(copy_url, {"copy_token": token, "result": "success"}).status_code, 403)
        event = AuditEvent.objects.filter(action="COPY", user=self.analyst).latest("sequence")
        self.assertEqual(event.metadata["context_id"], str(context.public_id))
        self.assertEqual(event.reason, context.reason)
        self.assertNotIn(COMPANY, event.reason + str(event.metadata))

    def test_expired_window_and_logout_require_new_authorization(self):
        client = self.authenticated_client(self.analyst)
        window, context = self.authorize_operation(client, self.analyst)
        window.expires_at = timezone.now() - timedelta(seconds=1)
        window.save(update_fields=["expires_at"])
        retry = client.post(reverse("vault:reveal", args=[self.card.pk]), {"field": "pan", "action": "reveal"})
        self.assertEqual(retry.status_code, 428)
        self.assertEqual(retry.json()["stage"], "identity")
        self.assertTrue(AuditEvent.objects.filter(action="OPERATION_WINDOW_EXPIRED", user=self.analyst).exists())
        context.refresh_from_db()
        self.assertIsNotNone(context.closed_at)
        client.post(reverse("logout"))
        window.refresh_from_db()
        self.assertIsNotNone(window.revoked_at)

    def test_other_session_cannot_reuse_identity_or_context(self):
        first = self.authenticated_client(self.analyst)
        window, context = self.authorize_operation(first, self.analyst)
        second = self.authenticated_client(self.analyst)
        response = second.post(reverse("vault:reveal", args=[self.card.pk]), {"field": "company", "action": "reveal"})
        self.assertEqual(response.status_code, 428)
        self.assertEqual(response.json()["stage"], "identity")
        self.assertNotEqual(session_hash(second.session.session_key), window.session_hash)
        self.assertNotEqual(session_hash(second.session.session_key), context.session_hash)

    def test_recipient_create_and_edit_need_no_manual_reason_but_are_audited(self):
        client = self.authenticated_client(self.admin)
        ReauthenticationGrant.objects.create(
            user=self.admin,
            session_hash=session_hash(client.session.session_key),
            purpose="policy_admin",
            expires_at=timezone.now() + timedelta(minutes=5),
        )
        payload = {
            "name": "Administración",
            "email": "admin@example.invalid",
            "alert_types": ["CRITICAL_ALERT", "EMAIL_FAILURE"],
            "minimum_severity": "HIGH",
            "delivery_mode": "IMMEDIATE",
            "active": "on",
            "is_primary": "on",
        }
        create = client.post(reverse("vault:recipients"), payload)
        self.assertRedirects(create, reverse("vault:recipients"), fetch_redirect_response=False)
        recipient = NotificationRecipient.objects.get()
        payload.update({"name": "Administración principal", "email": "nuevo@example.invalid"})
        edit = client.post(reverse("vault:recipient_edit", args=[recipient.pk]), payload)
        self.assertRedirects(edit, reverse("vault:recipients"), fetch_redirect_response=False)
        recipient.refresh_from_db()
        self.assertEqual(recipient.name, "Administración principal")
        events = AuditEvent.objects.filter(action="POLICY_CHANGED", metadata__recipient_id=recipient.pk)
        self.assertEqual(events.count(), 2)
        self.assertFalse(any(event.reason == "" for event in events))

    def test_leader_can_create_company_and_blank_edit_preserves_it(self):
        client = self.authenticated_client(self.leader)
        ReauthenticationGrant.objects.create(
            user=self.leader,
            session_hash=session_hash(client.session.session_key),
            purpose="cards_manage",
            expires_at=timezone.now() + timedelta(minutes=5),
        )
        create = client.post(reverse("vault:card_create"), {
            "client_name": "Cliente Nuevo",
            "cardholder_name": "Titular Nuevo",
            "brand": "VISA",
            "purpose": "Renovación",
            "active": "on",
            "pan": "4012888888881881",
            "expiry": "10/30",
            "company": "Empresa Nueva Protegida",
        })
        self.assertEqual(create.status_code, 302)
        created = PaymentCard.objects.get(client_name="Cliente Nuevo")
        self.assertNotIn("Empresa Nueva Protegida", created.encrypted_company)
        edit = client.post(reverse("vault:card_edit", args=[created.pk]), {
            "client_name": "Cliente Nuevo Editado",
            "cardholder_name": "Titular Nuevo",
            "brand": "VISA",
            "purpose": "Renovación",
            "company": "",
        })
        self.assertEqual(edit.status_code, 302)
        created.refresh_from_db()
        self.assertEqual(created.get_company(), "Empresa Nueva Protegida")
        self.assertFalse(AuditEvent.objects.filter(card=created, metadata__icontains="Empresa Nueva Protegida").exists())
