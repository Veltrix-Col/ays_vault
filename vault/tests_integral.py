import time
from datetime import timedelta
from io import BytesIO
from urllib.parse import parse_qs, urlparse

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django_otp.oath import TOTP
from django_otp.plugins.otp_totp.models import TOTPDevice
from openpyxl import load_workbook

from .crypto import encrypt
from .identity import has_recent_reauth
from .models import (
    AuditEvent,
    NotificationRecipient,
    NotificationRecord,
    PaymentCard,
    PendingSensitiveOperation,
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
CODE = "CODIGO-ULTRASECRETO-001"
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
            company_name=COMPANY,
            client_name="Cliente Seguro",
            cardholder_name="Titular Autorizado",
            identity_document="1000000001",
            email="titular@example.invalid",
            phone="+57 300 000 0001",
            brand="VISA",
            purpose="Operación autorizada",
            created_by=cls.leader,
        )
        cls.card.set_pan(PAN)
        cls.card.set_expiry("12/29")
        cls.card.set_code(CODE)
        cls.card.save()
        cls.other_card = PaymentCard(
            company_name="Otra Empresa S.A.S.",
            client_name="Segundo Cliente",
            cardholder_name="Otra Persona",
            brand="MC",
            purpose="Segunda operación",
            created_by=cls.leader,
        )
        cls.other_card.set_pan("5555555555554444")
        cls.other_card.set_expiry("09/30")
        cls.other_card.set_code("OTRO-CODIGO-PROTEGIDO")
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

    def authorize_operation(self, client, user, card=None, field="code", action="reveal", reason=None, reference="POL-123456", expect_identity=True):
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
                {"intent": intent, "password": PASSWORD},
            )
            self.assertEqual(identity.status_code, 200)
            self.assertContains(identity, "Relacione la consulta con la póliza")
        else:
            self.assertIn("Póliza", payload["form_html"])
            self.assertNotIn("Ingrese su contraseña", payload["form_html"])
        confirm = client.post(
            reverse("vault:protected_confirm"),
            {"intent": intent, "zoho_reference": reference},
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
        for query in (str(self.card.pk), COMPANY, "Cliente Seguro", "Titular Autorizado", "VISA", "1111"):
            response = client.get(url, {"q": query}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "Cliente Seguro")
            self.assertContains(response, COMPANY)
            self.assertNotContains(response, PAN)
            self.assertNotContains(response, CODE)
        empty = client.get(url, {"q": "inexistente"})
        self.assertContains(empty, "No se encontraron tarjetas")
        self.assertContains(empty, "Últimos cuatro dígitos")
        self.assertNotContains(empty, 'class="table vault-results-table"')
        self.assertNotContains(empty, 'class="empty-row"')
        rejected = client.get(url, {"q": PAN}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertNotContains(rejected, "Cliente Seguro")
        protected_code = client.get(url, {"q": CODE}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertNotContains(protected_code, "Cliente Seguro")
        self.assertContains(protected_code, "0 tarjetas")

    def test_company_is_administrative_and_code_is_encrypted_and_safe(self):
        stored = PaymentCard.objects.get(pk=self.card.pk)
        self.assertEqual(stored.company_name, COMPANY)
        self.assertNotIn(CODE, stored.encrypted_code)
        detail = self.authenticated_client(self.leader).get(reverse("vault:card_detail", args=[self.card.pk]))
        self.assertContains(detail, COMPANY)
        self.assertNotContains(detail, CODE)
        self.assertContains(detail, "Código")
        html = detail.content.decode()
        self.assertLess(
            html.index('data-protected-row="expiry"'),
            html.index('data-protected-row="code"'),
        )
        admin = self.authenticated_client(self.admin)
        export = admin.post(reverse("vault:export_report", args=["CARDS", "XLSX"]))
        workbook = load_workbook(BytesIO(export.content))
        text = " ".join(str(cell.value) for sheet in workbook for row in sheet for cell in row)
        self.assertNotIn(CODE, text)
        self.assertNotIn(PAN, text)

    def test_card_detail_shows_local_copyable_cardholder_before_protected_data(self):
        response = self.authenticated_client(self.leader).get(
            reverse("vault:card_detail", args=[self.card.pk])
        )
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertEqual(html.count(self.card.cardholder_name), 1)
        self.assertLess(
            html.index('class="administrative-data"'),
            html.index("<h2>Datos protegidos</h2>"),
        )
        holder_start = html.index('<section class="administrative-data"')
        holder_end = html.index(
            '<section class="panel protected-data-panel"', holder_start
        )
        holder_block = html[holder_start:holder_end]
        self.assertIn("Titular", holder_block)
        self.assertIn(self.card.cardholder_name, holder_block)
        self.assertIn("data-cardholder-value", holder_block)
        self.assertIn('data-copy-visible="visible-cardholder"', holder_block)
        self.assertIn('data-copy-visible="visible-document"', holder_block)
        self.assertIn('data-copy-visible="visible-email"', holder_block)
        self.assertIn('data-copy-visible="visible-phone"', holder_block)
        self.assertIn('type="button"', holder_block)
        self.assertIn('aria-live="polite"', holder_block)
        self.assertNotIn("Revelar", holder_block)
        self.assertNotIn("protected-action", holder_block)
        self.assertNotIn("data-field", holder_block)
        self.assertNotIn(PAN, html)
        self.assertNotIn(CODE, html)

    def test_historical_card_without_code_is_supported(self):
        historical = PaymentCard(company_name="Empresa Histórica", client_name="Histórica", cardholder_name="Titular", brand="VISA", purpose="Histórica", created_by=self.leader)
        historical.set_pan("4012888888881881")
        historical.set_expiry("11/30")
        historical.save()
        self.assertEqual(historical.get_code(), "")
        client = self.authenticated_client(self.leader)
        ReauthenticationGrant.objects.create(
            user=self.leader,
            session_hash=session_hash(client.session.session_key),
            purpose="cards_manage",
            expires_at=timezone.now() + timedelta(minutes=5),
        )
        response = client.get(reverse("vault:card_edit", args=[historical.pk]))
        self.assertContains(response, "EMP.")
        self.assertContains(response, "Empresa Histórica")
        self.assertContains(response, "Código")
        self.assertContains(response, "No configurado")
        self.assertRegex(
            response.content.decode(),
            r'<input[^>]*name="code"[^>]*\brequired\b',
        )
        self.assertContains(response, "•••• •••• ••••")
        self.assertContains(response, "••/••")
        self.assertNotContains(response, CODE)

    def test_first_operation_creates_identity_window_and_card_context(self):
        client = self.authenticated_client(self.analyst)
        window, context = self.authorize_operation(client, self.analyst)
        lifetime = (window.expires_at - window.created_at).total_seconds()
        self.assertGreaterEqual(lifetime, 3599)
        self.assertLessEqual(lifetime, 3601)
        self.assertFalse(hasattr(window, "reason"))
        self.assertFalse(hasattr(window, "internal_reference"))
        self.assertEqual(context.card, self.card)
        self.assertEqual(context.reason, "Consulta asociada a póliza")
        self.assertEqual(context.internal_reference, "POL-123456")

    def test_three_fields_of_same_card_share_context_and_audit_individually(self):
        client = self.authenticated_client(self.analyst)
        window, context = self.authorize_operation(client, self.analyst)
        for field, value in (("pan", PAN), ("expiry", "12/29"), ("code", CODE)):
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
        self.assertEqual(second_context.reason, "Consulta asociada a póliza")
        self.assertEqual(second_context.internal_reference, "REF-SEGUNDA")
        self.assertEqual(second_context.reason, first_context.reason)
        response = client.post(reverse("vault:reveal", args=[self.other_card.pk]), {"field": "expiry", "action": "reveal"})
        self.assertEqual(response.status_code, 200)
        event = AuditEvent.objects.filter(action="REVEAL", card=self.other_card).latest("sequence")
        self.assertEqual(event.reason, "Consulta asociada a póliza")
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
        self.assertEqual(second_context.reason, "Consulta asociada a póliza")

    def test_identity_must_succeed_before_context_and_context_rejects_protected_values(self):
        client = self.authenticated_client(self.analyst)
        start = client.post(reverse("vault:reveal", args=[self.card.pk]), {"field": "code", "action": "reveal"})
        intent = start.json()["intent"]
        bad = client.post(reverse("vault:protected_reauthenticate"), {"intent": intent, "password": "incorrecta"})
        self.assertEqual(bad.status_code, 422)
        self.assertNotContains(bad, "Póliza", status_code=422)
        premature = client.post(reverse("vault:protected_confirm"), {"intent": intent, "zoho_reference": "POL-1"})
        self.assertEqual(premature.status_code, 409)
        ok = client.post(reverse("vault:protected_reauthenticate"), {"intent": intent, "password": PASSWORD})
        self.assertEqual(ok.status_code, 200)
        protected = client.post(reverse("vault:protected_confirm"), {"intent": intent, "zoho_reference": f"Pago {COMPANY} 12/29"})
        self.assertEqual(protected.status_code, 422)
        self.assertTrue(SensitiveOperationWindow.objects.filter(user=self.analyst, revoked_at__isnull=True).exists())
        self.assertFalse(ProtectedOperationContext.objects.filter(user=self.analyst).exists())

    def test_copy_tokens_are_one_use_and_bound_to_window_session_and_user(self):
        client = self.authenticated_client(self.analyst)
        _, context = self.authorize_operation(client, self.analyst, action="copy")
        reveal = client.post(reverse("vault:reveal", args=[self.card.pk]), {"field": "code", "action": "copy"})
        token = reveal.headers["X-Vault-Copy-Token"]
        copy_url = reverse("vault:copy_event", args=[self.card.pk])
        self.assertEqual(client.post(copy_url, {"copy_token": token, "result": "success"}).status_code, 200)
        consumed = client.post(copy_url, {"copy_token": token, "result": "success"})
        self.assertEqual(consumed.status_code, 409)
        self.assertEqual(consumed.json()["error_code"], "REVEAL_GRANT_EXPIRED")
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
        response = second.post(reverse("vault:reveal", args=[self.card.pk]), {"field": "code", "action": "reveal"})
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
        self.assertTrue(recipient.active)
        toggle = client.post(reverse("vault:recipient_toggle", args=[recipient.pk]))
        self.assertRedirects(toggle, reverse("vault:recipients"), fetch_redirect_response=False)
        recipient.refresh_from_db()
        self.assertFalse(recipient.active)

    def test_admin_configuration_pages_are_simplified_and_history_is_paginated(self):
        client = self.authenticated_client(self.admin)
        policy_page = client.get(reverse("vault:policy_settings"))
        self.assertContains(policy_page, "<h1>Horarios</h1>", html=True)
        for obsolete in (
            "Operaciones que requieren reautenticación",
            "Inactividad y adopción",
            "Alertas y escalamiento",
            "Configuración de Seguridad",
        ):
            self.assertNotContains(policy_page, obsolete)

        holiday_page = client.get(reverse("vault:holidays"))
        self.assertNotContains(
            holiday_page,
            "Los festivos nacionales funcionan sin conexión. Los cambios manuales exigen motivo y reautenticación.",
        )

        for index in range(27):
            NotificationRecord.objects.create(
                notification_type="EMAIL_TEST",
                masked_recipient="a***@example.invalid",
                recipient_hash=f"{index:064d}",
                backend="console",
                result=NotificationRecord.SENT,
                idempotency_hash=f"{index + 100:064d}",
            )
        recipients_page = client.get(reverse("vault:recipients"))
        self.assertEqual(len(recipients_page.context["notification_page"]), 25)
        self.assertEqual(recipients_page.context["notification_page"].paginator.num_pages, 2)
        for obsolete in (
            "Backend activo", "Remitente", "Última prueba", "Escenario ficticio",
            "Error seguro", "<th>Acción</th>", "Tipos de alerta", "Severidad mínima",
        ):
            self.assertNotContains(recipients_page, obsolete, html=False)
        self.assertContains(recipients_page, "Nombre")
        self.assertContains(recipients_page, "Correo electrónico")
        second_page = client.get(reverse("vault:recipients"), {"page": 2})
        self.assertEqual(len(second_page.context["notification_page"]), 2)

    def test_leader_can_create_administrative_company_and_replace_code_securely(self):
        client = self.authenticated_client(self.leader)
        ReauthenticationGrant.objects.create(
            user=self.leader,
            session_hash=session_hash(client.session.session_key),
            purpose="cards_manage",
            expires_at=timezone.now() + timedelta(minutes=5),
        )
        create = client.post(reverse("vault:card_create"), {
            "company_name": "Empresa Nueva S.A.S.",
            "client_name": "Cliente Nuevo",
            "cardholder_name": "Titular Nuevo",
            "identity_document": "1000000002",
            "email": "nuevo@example.invalid",
            "phone": "+57 300 000 0002",
            "brand": "VISA",
            "purpose": "Renovación",
            "active": "on",
            "pan": "4012888888881881",
            "expiry": "10/30",
            "code": "CODIGO-NUEVO-PROTEGIDO",
        })
        self.assertEqual(create.status_code, 302)
        created = PaymentCard.objects.get(client_name="Cliente Nuevo")
        self.assertEqual(created.company_name, "Empresa Nueva S.A.S.")
        self.assertEqual(created.get_code(), "CODIGO-NUEVO-PROTEGIDO")
        self.assertNotIn("CODIGO-NUEVO-PROTEGIDO", created.encrypted_code)
        edit = client.post(reverse("vault:card_edit", args=[created.pk]), {
            "company_name": "Empresa Nueva Editada S.A.S.",
            "client_name": "Cliente Nuevo Editado",
            "cardholder_name": "Titular Nuevo",
            "identity_document": "1000000002",
            "email": "nuevo@example.invalid",
            "phone": "+57 300 000 0002",
            "brand": "VISA",
            "purpose": "Renovación",
            "code": "",
        })
        self.assertEqual(edit.status_code, 302)
        created.refresh_from_db()
        self.assertEqual(created.company_name, "Empresa Nueva Editada S.A.S.")
        self.assertEqual(created.get_code(), "CODIGO-NUEVO-PROTEGIDO")
        self.assertFalse(AuditEvent.objects.filter(card=created, metadata__icontains="CODIGO-NUEVO-PROTEGIDO").exists())
        replacement = "CODIGO-REEMPLAZADO-PROTEGIDO"
        edit = client.post(reverse("vault:card_edit", args=[created.pk]), {
            "company_name": "Empresa Nueva Editada S.A.S.",
            "client_name": "Cliente Nuevo Editado",
            "cardholder_name": "Titular Nuevo",
            "identity_document": "1000000002",
            "email": "nuevo@example.invalid",
            "phone": "+57 300 000 0002",
            "brand": "VISA",
            "purpose": "Renovación",
            "code": replacement,
        })
        self.assertEqual(edit.status_code, 302)
        created.refresh_from_db()
        self.assertEqual(created.get_code(), replacement)
        self.assertNotIn(replacement, created.encrypted_code)
        self.assertFalse(AuditEvent.objects.filter(card=created, metadata__icontains=replacement).exists())

    def test_card_create_survives_reauthentication_once_with_encrypted_pending_payload(self):
        client = self.authenticated_client(self.leader)
        session_key_before = client.session.session_key
        payload = {
            "company_name": "Empresa Pendiente S.A.S.",
            "client_name": "Cliente Pendiente",
            "cardholder_name": "Titular Pendiente",
            "identity_document": "1000000003",
            "email": "pendiente@example.invalid",
            "phone": "+57 300 000 0003",
            "brand": "VISA",
            "purpose": "Pago pendiente seguro",
            "active": "on",
            "pan": "4012888888881881",
            "expiry": "10/30",
            "code": "CODIGO-PENDIENTE-PROTEGIDO",
        }
        start = client.post(reverse("vault:card_create"), payload)
        self.assertEqual(start.status_code, 302)
        query = parse_qs(urlparse(start.url).query)
        operation_id = query["operation"][0]
        self.assertEqual(query["purpose"], ["cards_manage"])
        self.assertNotIn(payload["pan"], start.url)
        self.assertNotIn(payload["expiry"], start.url)
        self.assertNotIn(payload["code"], start.url)
        operation = PendingSensitiveOperation.objects.get(public_id=operation_id)
        self.assertNotIn(payload["pan"], operation.encrypted_payload)
        self.assertNotIn(payload["expiry"], operation.encrypted_payload)
        self.assertNotIn(payload["code"], operation.encrypted_payload)
        self.assertFalse(PaymentCard.objects.filter(client_name=payload["client_name"]).exists())

        reauth_page = client.get(start.url)
        self.assertContains(reauth_page, 'name="password"', count=1)
        self.assertNotContains(reauth_page, 'name="token"')
        self.assertNotContains(reauth_page, payload["pan"])
        finish = client.post(
            reverse("vault:reauthenticate"),
            {
                "purpose": "cards_manage",
                "operation": operation_id,
                "password": PASSWORD,
            },
        )
        created = PaymentCard.objects.get(client_name=payload["client_name"])
        self.assertRedirects(finish, reverse("vault:card_detail", args=[created.pk]), fetch_redirect_response=False)
        self.assertEqual(created.get_pan(), payload["pan"])
        self.assertEqual(created.get_expiry(), payload["expiry"])
        self.assertEqual(created.company_name, payload["company_name"])
        self.assertEqual(created.get_code(), payload["code"])
        operation.refresh_from_db()
        self.assertEqual(operation.status, PendingSensitiveOperation.COMPLETED)
        self.assertEqual(operation.encrypted_payload, "")
        self.assertEqual(
            AuditEvent.objects.filter(action="CREATE", metadata__operation_id=operation_id).count(),
            1,
        )
        created_audit = AuditEvent.objects.get(action="CREATE", metadata__operation_id=operation_id)
        self.assertNotIn(payload["code"], created_audit.reason + str(created_audit.metadata))
        self.assertEqual(client.session.session_key, session_key_before)
        self.assertFalse(
            AuditEvent.objects.filter(
                user=self.leader,
                action="SESSION_REPLACED",
            ).exists()
        )

        repeated = client.get(
            reverse("vault:reauthenticate"),
            {"purpose": "cards_manage", "operation": operation_id},
        )
        self.assertRedirects(repeated, reverse("vault:card_detail", args=[created.pk]), fetch_redirect_response=False)
        self.assertEqual(PaymentCard.objects.filter(client_name=payload["client_name"]).count(), 1)
        self.assertEqual(
            AuditEvent.objects.filter(action="CREATE", metadata__operation_id=operation_id).count(),
            1,
        )

    def test_global_fixed_window_allows_edit_and_deactivate_without_repeating_factors(self):
        client = self.authenticated_client(self.leader)
        authorize = client.post(
            reverse("vault:reauthenticate"),
            {
                "purpose": "cards_manage",
                "next": reverse("vault:card_detail", args=[self.card.pk]),
                "password": PASSWORD,
            },
        )
        self.assertEqual(authorize.status_code, 302)
        window = SensitiveOperationWindow.objects.get(user=self.leader, revoked_at__isnull=True)
        original_expiry = window.expires_at
        lifetime = (window.expires_at - window.created_at).total_seconds()
        self.assertGreaterEqual(lifetime, 3599)
        self.assertLessEqual(lifetime, 3601)

        edit_page = client.get(reverse("vault:card_edit", args=[self.card.pk]))
        self.assertEqual(edit_page.status_code, 200)
        self.assertContains(edit_page, self.card.masked_pan)
        self.assertNotContains(edit_page, PAN)
        self.assertNotContains(edit_page, "12/29")
        self.assertContains(edit_page, COMPANY)
        self.assertNotContains(edit_page, CODE)
        edit = client.post(
            reverse("vault:card_edit", args=[self.card.pk]),
            {
                "operation_id": "17c16ef0-292b-4fba-9f5e-98cdbb179cb5",
                "company_name": self.card.company_name,
                "client_name": "Alias actualizado",
                "cardholder_name": self.card.cardholder_name,
                "identity_document": self.card.identity_document,
                "email": self.card.email,
                "phone": self.card.phone,
                "brand": "VISA",
                "purpose": self.card.purpose,
                "code": "",
                "pan": "4222222222222",
                "expiry": "11/31",
            },
        )
        self.assertRedirects(edit, reverse("vault:card_detail", args=[self.card.pk]), fetch_redirect_response=False)
        self.card.refresh_from_db()
        self.assertEqual(self.card.get_pan(), "4222222222222")
        self.assertEqual(self.card.get_expiry(), "11/31")
        self.assertEqual(self.card.get_code(), CODE)

        deactivate = client.post(
            reverse("vault:card_deactivate", args=[self.card.pk]),
            {"operation_id": "2f63e7ba-76aa-421f-bdaa-1c7ba5075f2f"},
        )
        self.assertRedirects(deactivate, reverse("vault:card_list"), fetch_redirect_response=False)
        self.card.refresh_from_db()
        self.assertFalse(self.card.active)
        window.refresh_from_db()
        self.assertEqual(window.expires_at, original_expiry)
        self.assertEqual(SensitiveOperationWindow.objects.filter(user=self.leader, revoked_at__isnull=True).count(), 1)

    def test_reveal_window_covers_create_edit_and_deactivate_without_rotating_session(self):
        client = self.authenticated_client(self.leader)
        session_key_before = client.session.session_key
        window, _ = self.authorize_operation(
            client,
            self.leader,
            field="pan",
            reason="Operación integral autorizada",
            reference="QA-WINDOW-001",
        )
        original_expiry = window.expires_at
        self.assertEqual(
            client.post(
                reverse("vault:reveal", args=[self.card.pk]),
                {"field": "pan", "action": "reveal"},
            ).status_code,
            200,
        )

        create = client.post(
            reverse("vault:card_create"),
            {
                "operation_id": "64e121f0-a5a6-4c60-807e-41d24af57a91",
                "company_name": "Empresa transversal S.A.S.",
                "client_name": "Tarjeta ventana transversal",
                "cardholder_name": "Titular transversal",
                "identity_document": "1000000004",
                "email": "transversal@example.invalid",
                "phone": "+57 300 000 0004",
                "brand": "VISA",
                "purpose": "Operación transversal",
                "active": "on",
                "pan": "4000000000000002",
                "expiry": "10/31",
                "code": "CODIGO-TRANSVERSAL",
            },
        )
        created = PaymentCard.objects.get(client_name="Tarjeta ventana transversal")
        self.assertRedirects(
            create,
            reverse("vault:card_detail", args=[created.pk]),
            fetch_redirect_response=False,
        )

        edit = client.post(
            reverse("vault:card_edit", args=[created.pk]),
            {
                "operation_id": "f497ebc3-f094-4bc3-a843-910ea8c3dbcc",
                "company_name": "Empresa transversal S.A.S.",
                "client_name": "Tarjeta transversal editada",
                "cardholder_name": "Titular transversal",
                "identity_document": "1000000004",
                "email": "transversal@example.invalid",
                "phone": "+57 300 000 0004",
                "brand": "VISA",
                "purpose": "Operación transversal",
                "code": "",
                "pan": "4222222222222",
                "expiry": "11/31",
            },
        )
        self.assertRedirects(
            edit,
            reverse("vault:card_detail", args=[created.pk]),
            fetch_redirect_response=False,
        )
        deactivate = client.post(
            reverse("vault:card_deactivate", args=[created.pk]),
            {"operation_id": "113231be-7444-4bb2-b9fb-b5e3f6595721"},
        )
        self.assertRedirects(
            deactivate,
            reverse("vault:card_list"),
            fetch_redirect_response=False,
        )

        window.refresh_from_db()
        created.refresh_from_db()
        self.assertFalse(created.active)
        self.assertEqual(window.expires_at, original_expiry)
        self.assertEqual(client.session.session_key, session_key_before)
        self.assertEqual(
            SensitiveOperationWindow.objects.filter(
                user=self.leader,
                revoked_at__isnull=True,
            ).count(),
            1,
        )
        events = AuditEvent.objects.filter(user=self.leader)
        self.assertEqual(events.filter(action="REAUTH_SUCCESS").count(), 1)
        self.assertEqual(events.filter(action="OPERATION_AUTHORIZED").count(), 1)
        self.assertEqual(events.filter(action="CREATE", card=created).count(), 1)
        self.assertEqual(events.filter(action="UPDATE", card=created).count(), 1)
        self.assertEqual(events.filter(action="DEACTIVATE", card=created).count(), 1)
        self.assertFalse(events.filter(action="SESSION_REPLACED").exists())
        self.assertFalse(events.filter(action="LOGOUT").exists())

    def test_async_reveal_with_invalid_secure_session_returns_safe_json_not_login_html(self):
        client = self.authenticated_client(self.leader)
        client.get(reverse("vault:card_detail", args=[self.card.pk]))
        SecureSession.objects.filter(
            user=self.leader,
            status=SecureSession.ACTIVE,
        ).update(
            status=SecureSession.REVOKED,
            revoked_at=timezone.now(),
            revocation_reason="Prueba de contrato AJAX",
        )
        response = client.post(
            reverse("vault:reveal", args=[self.card.pk]),
            {"field": "pan", "action": "reveal"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error_code"], "SECURE_SESSION_INVALID")
        self.assertEqual(response.headers["X-Vault-Auth-Required"], "1")
        self.assertNotIn("<html", response.content.decode().lower())
        self.assertNotIn("name=\"password\"", response.content.decode().lower())

    def test_protected_endpoints_return_safe_status_contracts(self):
        client = self.authenticated_client(self.analyst)
        invalid = client.post(
            reverse("vault:reveal", args=[self.card.pk]),
            {"field": "unknown", "action": "reveal"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(invalid.json()["error_code"], "INVALID_PROTECTED_ACTION")

        missing = client.post(
            reverse("vault:reveal", args=[999999]),
            {"field": "pan", "action": "reveal"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json()["error_code"], "CARD_NOT_AVAILABLE")

        expired_intent = client.post(
            reverse("vault:protected_confirm"),
            {
                "intent": "expired-intent",
                "zoho_reference": "QA-001",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(expired_intent.status_code, 409)
        self.assertEqual(
            expired_intent.json()["error_code"],
            "PROTECTED_INTENT_EXPIRED",
        )

    def test_expired_global_window_requires_reauthentication_for_next_card_change(self):
        client = self.authenticated_client(self.leader)
        window = SensitiveOperationWindow.objects.create(
            user=self.leader,
            session_hash=session_hash(client.session.session_key),
            purpose="sensitive_operations",
            expires_at=timezone.now() - timedelta(microseconds=1),
        )
        response = client.post(
            reverse("vault:card_edit", args=[self.card.pk]),
            {
                "client_name": self.card.client_name,
                "company_name": self.card.company_name,
                "cardholder_name": self.card.cardholder_name,
                "identity_document": self.card.identity_document,
                "email": self.card.email,
                "phone": self.card.phone,
                "brand": self.card.brand,
                "purpose": self.card.purpose,
                "code": "",
                "pan": "",
                "expiry": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("purpose=cards_manage", response.url)
        window.refresh_from_db()
        self.assertIsNotNone(window.revoked_at)

    def test_vault_results_are_compact_safe_and_search_reference(self):
        client = self.authenticated_client(self.leader)
        response = client.get(reverse("vault:card_list"), {"q": "Operación autorizada"})
        self.assertContains(response, 'class="table vault-results-table"')
        self.assertContains(response, 'data-label="Emp."')
        self.assertContains(response, COMPANY)
        self.assertContains(response, "Titular Autorizado")
        self.assertContains(response, "•••• 1111")
        self.assertNotContains(response, PAN)
        self.assertNotContains(response, CODE)
        self.assertContains(response, "No admite número completo, vencimiento ni código.")

    def test_invalid_card_form_does_not_echo_protected_values(self):
        client = self.authenticated_client(self.leader)
        response = client.post(
            reverse("vault:card_create"),
            {
                "company_name": COMPANY,
                "client_name": "Alias inválido",
                "cardholder_name": "Titular",
                "identity_document": "1000000005",
                "email": "invalido@example.invalid",
                "phone": "+57 300 000 0005",
                "brand": "MC",
                "purpose": "Referencia",
                "active": "on",
                "pan": "4012888888881881",
                "expiry": "12/29",
                "code": CODE,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "4012888888881881")
        self.assertNotContains(response, "12/29")
        self.assertContains(response, COMPANY)
        self.assertNotContains(response, CODE)
        self.assertContains(response, "La franquicia no coincide")

    def test_new_card_renders_administrative_company_and_required_code(self):
        response = self.authenticated_client(self.leader).get(reverse("vault:card_create"))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        protected_section = html[html.index('id="card-protected-title"'):]
        self.assertIn('name="pan"', protected_section)
        self.assertIn('name="expiry"', protected_section)
        self.assertIn('name="code"', protected_section)
        self.assertIn(">Código", protected_section)
        self.assertNotIn('name="company_name"', protected_section)
        self.assertLess(protected_section.index('name="pan"'), protected_section.index('name="code"'))
        self.assertLess(protected_section.index('name="expiry"'), protected_section.index('name="code"'))
        self.assertRegex(
            protected_section,
            r'<input[^>]*name="code"[^>]*\brequired\b',
        )
        administrative_section = html[
            html.index('id="card-general-title"'):html.index('id="card-protected-title"')
        ]
        self.assertIn('name="company_name"', administrative_section)
        self.assertIn('placeholder="Razón social"', administrative_section)
        self.assertIn(">EMP.", administrative_section)
        self.assertNotIn("Empresa asociada", administrative_section)
        self.assertIn('class="form-grid card-administrative-grid"', administrative_section)

    def test_edit_card_renders_company_admin_and_code_masked_replacement(self):
        response = self.authenticated_client(self.leader).get(
            reverse("vault:card_edit", args=[self.card.pk])
        )
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        administrative_section = html[
            html.index('id="card-general-title"'):html.index('id="card-protected-title"')
        ]
        protected_section = html[html.index('id="card-protected-title"'):]
        self.assertIn('name="company_name"', administrative_section)
        self.assertIn(COMPANY, administrative_section)
        self.assertNotIn(CODE, html)
        self.assertNotIn("EMP.", protected_section)
        self.assertIn('data-protected-edit-toggle="protected-code-edit"', protected_section)
        self.assertIn('id="protected-code-edit"', protected_section)
        self.assertIn('name="code"', protected_section)
        self.assertLess(
            protected_section.index('data-protected-edit-toggle="protected-pan-edit"'),
            protected_section.index('data-protected-edit-toggle="protected-expiry-edit"'),
        )
        self.assertLess(
            protected_section.index('data-protected-edit-toggle="protected-expiry-edit"'),
            protected_section.index('data-protected-edit-toggle="protected-code-edit"'),
        )

    def test_sensitive_window_is_shared_across_authorized_purposes(self):
        client = self.authenticated_client(self.leader)
        response = client.post(
            reverse("vault:reauthenticate"),
            {
                "purpose": "cards_manage",
                "next": reverse("vault:card_list"),
                "password": PASSWORD,
            },
        )
        self.assertEqual(response.status_code, 302)
        window = SensitiveOperationWindow.objects.get(user=self.leader, revoked_at__isnull=True)
        request = client.get(reverse("vault:card_list")).wsgi_request
        self.assertTrue(has_recent_reauth(request, "cards_manage"))
        self.assertTrue(has_recent_reauth(request, "identity_admin"))
        window.refresh_from_db()
        self.assertEqual(
            SensitiveOperationWindow.objects.filter(user=self.leader, revoked_at__isnull=True).count(),
            1,
        )
