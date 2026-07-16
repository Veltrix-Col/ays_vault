from io import BytesIO
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django_otp.plugins.otp_totp.models import TOTPDevice
from openpyxl import load_workbook

from .forms import TimelineFilterForm
from .crypto import encrypt
from .models import AuditEvent, PaymentCard, PolicyConfiguration, ReportExport, SecureSession, UserProfile
from .reporting import excel_safe
from .security import audit, session_hash, verify_audit_chain


PASSWORD = "ReportsSecure123!"
TEST_SETTINGS = dict(
    APP_ENV="development",
    FIELD_ENCRYPTION_KEY="MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
    FIELD_FINGERPRINT_KEY="reports-test-key",
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
    ALERT_EMAIL_BACKEND="console",
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    AXES_ENABLED=False,
)


@override_settings(**TEST_SETTINGS)
class ReportingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.admin = User.objects.create_user("admin.reports", password=PASSWORD, first_name="Ana")
        cls.leader = User.objects.create_user("leader.reports", password=PASSWORD, first_name="Laura")
        cls.analyst = User.objects.create_user("analyst.reports", password=PASSWORD, first_name="Andrés")
        for user, role in ((cls.admin, UserProfile.ADMIN), (cls.leader, UserProfile.LEADER), (cls.analyst, UserProfile.ANALYST)):
            profile = user.vault_profile
            profile.role, profile.active, profile.mfa_enabled, profile.mfa_status = role, True, True, UserProfile.MFA_ACTIVE
            profile.save()
            TOTPDevice.objects.create(user=user, confirmed=True)
        cls.card = PaymentCard(client_name="=SUM(1,1)", cardholder_name="Alias seguro", brand="VISA", purpose="Uso interno", created_by=cls.leader)
        cls.card.set_pan("4111111111111111")
        cls.card.set_expiry("12/29")
        cls.card.save()

    def setUp(self):
        PolicyConfiguration.objects.get_or_create(singleton=1)

    def login(self, user):
        client = Client(REMOTE_ADDR=f"10.20.0.{user.pk}", HTTP_USER_AGENT=f"Report Test Browser {user.pk}")
        client.force_login(user)
        session = client.session
        session["otp_device_id"] = TOTPDevice.objects.get(user=user).persistent_id
        session.save()
        now = timezone.now()
        SecureSession.objects.update_or_create(session_hash=session_hash(session.session_key), defaults={
            "user": user, "encrypted_session_key": encrypt(session.session_key), "last_activity_at": now,
            "expires_at": now + timedelta(minutes=10), "initial_ip": f"10.20.0.{user.pk}",
            "last_ip": f"10.20.0.{user.pk}", "user_agent": f"Report Test Browser {user.pk}",
            "status": SecureSession.ACTIVE, "mfa_completed": True, "mfa_completed_at": now,
        })
        return client

    def test_timeline_form_validates_dates_ip_and_rejects_pan(self):
        invalid_date = TimelineFilterForm({"date_from": "2026-07-15", "date_to": "2026-07-01"}, user=self.admin)
        self.assertFalse(invalid_date.is_valid())
        invalid_ip = TimelineFilterForm({"ip": "dirección-no-válida"}, user=self.admin)
        self.assertFalse(invalid_ip.is_valid())
        pan = TimelineFilterForm({"card": "4111111111111111"}, user=self.admin)
        self.assertFalse(pan.is_valid())

    def test_quick_filter_chips_and_responsive_controls_render(self):
        audit(None, "COPY", user=self.admin)
        response = self.login(self.admin).get(reverse("vault:timeline"), {"period": "7d", "quick_event": "copies", "page_size": "25"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Últimos 7 días")
        self.assertContains(response, "Más filtros")
        self.assertContains(response, "active-filter-chip")
        self.assertContains(response, "Exportar Excel")
        self.assertEqual(response.context["page"].paginator.per_page, 25)

    def test_analyst_timeline_and_report_are_scoped_to_self(self):
        own = audit(None, "VIEW", user=self.analyst, card=self.card)
        other = audit(None, "VIEW", user=self.leader, card=self.card)
        client = self.login(self.analyst)
        response = client.get(reverse("vault:timeline"), {"user": self.leader.pk})
        ids = {event.pk for event in response.context["page"]}
        self.assertNotIn(other.pk, ids)
        self.assertFalse(ids, "Un filtro de usuario ajeno debe rechazarse sin revelar resultados.")
        export = client.post(reverse("vault:export_report", args=["TIMELINE", "XLSX"]))
        workbook = load_workbook(BytesIO(export.content))
        values = list(workbook["Datos"].values)
        self.assertTrue(any("Andrés" in str(row) for row in values))
        self.assertFalse(any("leader.reports" in str(row) for row in values))

    def test_excel_is_valid_styled_filtered_and_audited(self):
        audit(None, "COPY", user=self.admin, reason="Motivo seguro")
        response = self.login(self.admin).post(reverse("vault:export_report", args=["TIMELINE", "XLSX"]), {"quick_event": "copies"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        workbook = load_workbook(BytesIO(response.content), data_only=False)
        self.assertEqual(workbook.sheetnames, ["Resumen", "Datos"])
        self.assertEqual(workbook["Datos"].freeze_panes, "A2")
        self.assertTrue(workbook["Datos"].auto_filter.ref)
        content = " ".join(str(cell.value) for sheet in workbook for row in sheet for cell in row)
        self.assertNotIn("4111111111111111", content)
        self.assertNotIn("12/29", content)
        self.assertTrue(ReportExport.objects.filter(user=self.admin, result="SUCCESS", export_format="XLSX").exists())
        self.assertTrue(AuditEvent.objects.filter(user=self.admin, action="REPORT_EXPORT").exists())
        self.assertTrue(verify_audit_chain()[0])

    def test_formula_injection_is_neutralized_in_card_report(self):
        response = self.login(self.leader).post(reverse("vault:export_report", args=["CARDS", "XLSX"]))
        self.assertEqual(response.status_code, 200)
        workbook = load_workbook(BytesIO(response.content), data_only=False)
        values = [cell.value for row in workbook["Datos"] for cell in row]
        self.assertIn("'=SUM(1,1)", values)
        self.assertFalse(any(cell.data_type == "f" for row in workbook["Datos"] for cell in row))

    def test_pdf_is_valid_private_and_contains_no_sensitive_values(self):
        audit(None, "REVEAL", user=self.admin, reason="Consulta autorizada")
        response = self.login(self.admin).post(reverse("vault:export_report", args=["TIMELINE", "PDF"]), {"quick_event": "reveals"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content[:4], b"%PDF")
        self.assertIn("no-store", response["Cache-Control"])
        self.assertNotIn(b"4111111111111111", response.content)
        self.assertNotIn(b"12/29", response.content)
        self.assertTrue(ReportExport.objects.filter(user=self.admin, result="SUCCESS", export_format="PDF").exists())

    def test_role_matrix_blocks_admin_cards_and_allows_leader(self):
        admin = self.login(self.admin)
        leader = self.login(self.leader)
        self.assertEqual(admin.post(reverse("vault:export_report", args=["CARDS", "XLSX"])).status_code, 403)
        self.assertEqual(leader.post(reverse("vault:export_report", args=["CARDS", "XLSX"])).status_code, 200)
        self.assertNotContains(admin.get(reverse("vault:report_center")), "Informe Seguro de Tarjetas")
        self.assertContains(leader.get(reverse("vault:report_center")), "Inventario operativo")

    def test_reports_cannot_be_generated_by_get(self):
        client = self.login(self.admin)
        self.assertEqual(client.get(reverse("vault:export_report", args=["TIMELINE", "XLSX"])).status_code, 405)

    def test_export_post_requires_csrf_token(self):
        authenticated = self.login(self.admin)
        protected = Client(enforce_csrf_checks=True, REMOTE_ADDR=f"10.20.0.{self.admin.pk}")
        protected.cookies = authenticated.cookies
        response = protected.post(reverse("vault:export_report", args=["TIMELINE", "XLSX"]))
        self.assertEqual(response.status_code, 403)
        self.assertFalse(ReportExport.objects.exists())

    @override_settings(REPORT_XLSX_MAX_ROWS=1)
    def test_export_limit_requires_narrower_filters(self):
        audit(None, "ACCESS", user=self.admin)
        audit(None, "ACCESS", user=self.admin)
        response = self.login(self.admin).post(reverse("vault:export_report", args=["TIMELINE", "XLSX"]))
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "límite seguro", status_code=400)
        self.assertTrue(ReportExport.objects.filter(result="LIMITED", safe_error="ROW_LIMIT_EXCEEDED").exists())

    def test_excel_safe_helper_neutralizes_all_formula_prefixes(self):
        for value in ("=1+1", "+cmd", "-2+3", "@SUM(A1:A2)"):
            self.assertTrue(excel_safe(value).startswith("'"))
