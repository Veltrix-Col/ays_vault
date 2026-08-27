from datetime import date
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Permission
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from vault.crypto import encrypt

from cotizacion_colectivos.models import ColectivosOperationalSetting, RenovacionColectiva
from cotizacion_colectivos.management.commands.colectivos_process_renewals import Command
from cotizacion_colectivos.services.renewals import RenewalPolicy, diagnose_renewal_source, list_collective_renewals, process_renewal_cycles, sync_renewal_cycles, _map_record, upcoming_cycles, tracking_cycles, set_renewal_selection


class RenewalReadContractTests(SimpleTestCase):
    def test_active_monthly_allowed_policy_is_eligible_even_with_annual_contract_term(self):
        included = _map_record({"id": "4991513000271000001", "Name": "0400", "Ramo": "VG patronal", "Estado_de_la_p_liza": "Vigente", "Frecuencia": "Mensual", "Modo_de_pago": "Fraccionado", "Periodicidad_de_pago": None, "P_liza_Fecha_de_inicio_vigencia": "2026-01-01", "P_liza_Fecha_fin_de_la_vigencia": "2026-12-31", "Tomador_principal1": "Empresa", "Correo_gesti_n_comercial": "empresa@example.test"}, today=date(2026, 8, 24), window=30)
        self.assertIsNotNone(included)
        self.assertEqual(included.scheduled_for, date(2026, 8, 31))
        self.assertEqual(included.monthly_period, "2026-09")
        self.assertIsNone(_map_record({"id": "1", "Ramo": "VG patronal", "Estado_de_la_p_liza": "Cancelada", "Frecuencia": "Mensual"}, today=date(2026, 8, 24), window=30))
        self.assertIsNone(_map_record({"id": "2", "Ramo": "VG patronal", "Estado_de_la_p_liza": "Vigente", "Frecuencia": "Trimestral"}, today=date(2026, 8, 24), window=30))
        self.assertIsNone(_map_record({"id": "4991513000271000007", "Ramo": "Salud colectivo", "Estado_de_la_p_liza": "Vigente", "Frecuencia": "Mensual"}, today=date(2026, 8, 24), window=30))

    def test_monthly_period_is_the_following_month_across_year_boundary(self):
        december = _map_record({"id": "4991513000271000005", "Name": "0401", "Ramo": "AP colectivo", "Estado_de_la_p_liza": "Vigente", "Frecuencia": "Mensual"}, today=date(2026, 12, 15), window=30)
        self.assertEqual(december.monthly_period, "2027-01")
        self.assertEqual(december.scheduled_for, date(2026, 12, 31))

    def test_monthly_period_and_schedule_follow_monthly_send_boundary(self):
        september = _map_record({"id": "4991513000271000006", "Name": "0402", "Ramo": "VG deudores", "Estado_de_la_p_liza": "Vigente", "Frecuencia": "Mensual"}, today=date(2026, 9, 30), window=30)
        self.assertEqual(september.monthly_period, "2026-10")
        self.assertEqual(september.scheduled_for, date(2026, 9, 30))

    def test_list_uses_server_side_collective_criteria(self):
        class Search:
            def __init__(self): self.criteria = None
            def by_criteria(self, **kwargs):
                self.criteria = kwargs["criteria"]
                return SimpleNamespace(records=[{"id": "4991513000271000001", "Name": "0400", "Ramo": "AP colectivo", "Estado_de_la_p_liza": "Vigente", "Frecuencia": "Mensual", "P_liza_Fecha_fin_de_la_vigencia": "2026-09-10"}], more_records=False)
        search = Search()
        fake = SimpleNamespace(search=search)
        rows = list_collective_renewals(zoho=fake, today=date(2026, 8, 24))
        self.assertEqual(len(rows), 1)
        self.assertIn("Ramo:equals:", search.criteria)

    def test_empty_exact_search_does_not_sweep_all_policies_in_interactive_read(self):
        class Search:
            def by_criteria(self, **kwargs):
                return SimpleNamespace(records=(), more_records=False)

        class Records:
            def list(self, **kwargs):
                raise AssertionError("La pantalla no debe barrer Polizas ante una búsqueda vacía")

        fake = SimpleNamespace(search=Search(), records=Records())
        rows = list_collective_renewals(zoho=fake, today=date(2026, 8, 24))
        self.assertEqual(rows, ())

    def test_collective_variants_are_combined_and_deduplicated(self):
        class Search:
            def by_criteria(self, **kwargs):
                value = kwargs["criteria"].rsplit(":", 1)[-1].rstrip(")")
                if value == "Colectivo":
                    return SimpleNamespace(records=(), more_records=False)
                record = {
                    "id": "4991513000271000004", "Name": "0403",
                    "Ramo": value, "Estado_de_la_p_liza": "Vigente", "Frecuencia": "Mensual",
                    "P_liza_Fecha_fin_de_la_vigencia": "2026-09-10",
                }
                return SimpleNamespace(records=(record,), more_records=False)

        fake = SimpleNamespace(search=Search())
        rows = list_collective_renewals(zoho=fake, today=date(2026, 8, 24))
        self.assertEqual(len(rows), 1)

    def test_diagnostic_reports_filter_stages_without_writes(self):
        class Search:
            def by_criteria(self, **kwargs):
                return SimpleNamespace(records=(), more_records=False)

        class Records:
            def list(self, **kwargs):
                return SimpleNamespace(records=({
                    "id": "4991513000271000003", "Name": "0402", "Ramo": "VG deudores",
                    "Estado_de_la_p_liza": "Vigente", "Frecuencia": "Mensual",
                    "P_liza_Fecha_fin_de_la_vigencia": "2026-09-10",
                    "Tomador_principal1": {"name": "Empresa"},
                },), more_records=False)

        diagnostic = diagnose_renewal_source(
            zoho=SimpleNamespace(search=Search(), records=Records()),
            today=date(2026, 8, 24), window=30,
        )
        self.assertEqual(diagnostic["total_records"], 1)
        self.assertEqual(diagnostic["collective_records"], 1)
        self.assertEqual(diagnostic["valid_expiry_records"], 1)
        self.assertEqual(diagnostic["next_30_days"], 1)


@override_settings(COLECTIVOS_INTERNAL_PUBLIC_ACCESS=False)
class RenewalSelectionTests(TestCase):
    def _notification_manager(self):
        user = get_user_model().objects.create_user("renewal-manager", password="Password123!")
        permission = Permission.objects.get(
            codename="manage_notifications",
            content_type__app_label="cotizacion_colectivos",
        )
        user.user_permissions.add(permission)
        self.client.force_login(user)
        return user

    def test_monthly_switch_authorized_toggles_and_persists_on_get(self):
        self._notification_manager()
        endpoint = reverse("cotizacion_colectivos:monthly_renewals_toggle")
        response = self.client.post(endpoint, {"enabled": "1", "next": reverse("cotizacion_colectivos:renewal_tracking")})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("cotizacion_colectivos:renewal_tracking"))
        self.assertTrue(ColectivosOperationalSetting.objects.get(key="monthly_renewals_enabled").enabled)

        with patch("cotizacion_colectivos.views.sync_renewal_cycles", return_value=()), patch(
            "cotizacion_colectivos.views.upcoming_cycles", return_value=()
        ), patch("cotizacion_colectivos.views.renewal_dashboard_counts", return_value={}):
            page = self.client.get(reverse("cotizacion_colectivos:novelties_index"))
        self.assertContains(page, "ON · Automatización activa")

        response = self.client.post(endpoint, {"enabled": "0", "next": reverse("cotizacion_colectivos:renewal_tracking")})
        self.assertEqual(response["Location"], reverse("cotizacion_colectivos:renewal_tracking"))
        self.assertFalse(ColectivosOperationalSetting.objects.get(key="monthly_renewals_enabled").enabled)

    def test_monthly_switch_unauthorized_cannot_change_and_get_is_read_only(self):
        user = get_user_model().objects.create_user("renewal-viewer", password="Password123!")
        self.client.force_login(user)
        endpoint = reverse("cotizacion_colectivos:monthly_renewals_toggle")
        response = self.client.post(endpoint, {"enabled": "1"})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(ColectivosOperationalSetting.objects.filter(key="monthly_renewals_enabled", enabled=True).exists())

    @override_settings(
        COLECTIVOS_INTERNAL_PUBLIC_ACCESS=True,
        COLECTIVOS_TECHNICAL_ACTOR_USERNAME="renewal-technical-actor",
    )
    def test_monthly_switch_sso_actor_does_not_assign_anonymous_user(self):
        response = self.client.post(
            reverse("cotizacion_colectivos:monthly_renewals_toggle"),
            {"enabled": "1"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith("/cotizacion-colectivos/"))
        setting = ColectivosOperationalSetting.objects.get(key="monthly_renewals_enabled")
        self.assertTrue(setting.enabled)
        self.assertEqual(setting.updated_by.username, "renewal-technical-actor")
        self.assertNotIsInstance(setting.updated_by, AnonymousUser)

        with patch("cotizacion_colectivos.views.sync_renewal_cycles", return_value=()), patch(
            "cotizacion_colectivos.views.upcoming_cycles", return_value=()
        ), patch("cotizacion_colectivos.views.renewal_dashboard_counts", return_value={}):
            page = self.client.get(reverse("cotizacion_colectivos:novelties_index"))
        self.assertEqual(page.status_code, 200)
        self.assertTrue(ColectivosOperationalSetting.objects.get(key="monthly_renewals_enabled").enabled)

        response = self.client.post(
            reverse("cotizacion_colectivos:monthly_renewals_toggle"),
            {"enabled": "0"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(ColectivosOperationalSetting.objects.get(key="monthly_renewals_enabled").enabled)

    def _policy(self, email):
        return RenewalPolicy(
            remote_id="4991513000271000099", token="policy-token", policy="0400",
            client="Empresa", branch="VG deudores", expiry_date=date(2027, 8, 26),
            email=email, policy_status="Vigente", payment_frequency="Mensual",
            monthly_period="2026-09", scheduled_for=date(2026, 8, 31),
        )

    @override_settings(COLECTIVOS_RENEWAL_READ_CACHE_SECONDS=0)
    def test_sync_refreshes_programmed_recipient_before_first_send(self):
        cycle = RenovacionColectiva.objects.create(
            cycle_key="4991513000271000099:2026-09", policy_remote_id="4991513000271000099",
            policy_token="protected", masked_policy="0400", client_label="Empresa", branch_name="VG deudores",
            monthly_period="2026-09", scheduled_for=date(2026, 8, 31), status=RenovacionColectiva.Status.PROGRAMMED,
            encrypted_recipient=encrypt("old@example.test"), recipient_hash="old",
        )
        with patch("cotizacion_colectivos.services.renewals.list_collective_renewals", return_value=(self._policy("new@example.test"),)):
            sync_renewal_cycles(zoho=object(), today=date(2026, 8, 24))
        cycle.refresh_from_db()
        self.assertEqual(cycle.recipient_email, "new@example.test")

    def test_new_cycle_records_automation_eligibility_at_sync_time(self):
        policy = self._policy("new@example.test")
        with patch("cotizacion_colectivos.services.renewals.monthly_renewals_enabled", return_value=False), patch(
            "cotizacion_colectivos.services.renewals.list_collective_renewals", return_value=(policy,)
        ):
            sync_renewal_cycles(zoho=object(), today=date(2026, 8, 24))
        cycle = RenovacionColectiva.objects.get(cycle_key="4991513000271000099:2026-09")
        self.assertFalse(cycle.automation_eligible)

        policy = RenewalPolicy(**{**policy.__dict__, "email": "newer@example.test"})
        with patch("cotizacion_colectivos.services.renewals.monthly_renewals_enabled", return_value=True), patch(
            "cotizacion_colectivos.services.renewals.list_collective_renewals", return_value=(policy,)
        ):
            sync_renewal_cycles(zoho=object(), today=date(2026, 8, 24))
        cycle.refresh_from_db()
        self.assertFalse(cycle.automation_eligible)

    def test_new_cycle_is_automation_eligible_when_synced_enabled(self):
        policy = RenewalPolicy(**{**self._policy("new@example.test").__dict__, "remote_id": "4991513000271000100"})
        with patch("cotizacion_colectivos.services.renewals.monthly_renewals_enabled", return_value=True), patch(
            "cotizacion_colectivos.services.renewals.list_collective_renewals", return_value=(policy,)
        ):
            sync_renewal_cycles(zoho=object(), today=date(2026, 8, 24))
        self.assertTrue(RenovacionColectiva.objects.get(cycle_key="4991513000271000100:2026-09").automation_eligible)

    def test_sync_clears_programmed_recipient_when_zoho_email_is_empty(self):
        cycle = RenovacionColectiva.objects.create(
            cycle_key="4991513000271000098:2026-09", policy_remote_id="4991513000271000098",
            policy_token="protected", masked_policy="0401", client_label="Empresa", branch_name="VG deudores",
            monthly_period="2026-09", scheduled_for=date(2026, 8, 31), status=RenovacionColectiva.Status.PROGRAMMED,
            encrypted_recipient=encrypt("old@example.test"), recipient_hash="old",
        )
        policy = self._policy("")
        policy = RenewalPolicy(**{**policy.__dict__, "remote_id": cycle.policy_remote_id})
        with patch("cotizacion_colectivos.services.renewals.list_collective_renewals", return_value=(policy,)):
            sync_renewal_cycles(zoho=object(), today=date(2026, 8, 24))
        cycle.refresh_from_db()
        self.assertEqual(cycle.recipient_email, "")
        self.assertEqual(cycle.recipient_hash, "")

    def test_sync_preserves_recipient_for_sent_and_responded_cycles(self):
        for status, suffix in ((RenovacionColectiva.Status.SENT, "sent"), (RenovacionColectiva.Status.RESPONDED, "responded")):
            with self.subTest(status=status):
                cycle = RenovacionColectiva.objects.create(
                    cycle_key=f"49915130002710000{90 if status == RenovacionColectiva.Status.SENT else 91}:2026-09",
                    policy_remote_id=f"49915130002710000{90 if status == RenovacionColectiva.Status.SENT else 91}",
                    policy_token="protected", masked_policy="0402", client_label="Empresa", branch_name="VG deudores",
                    monthly_period="2026-09", scheduled_for=date(2026, 8, 31), status=status,
                    encrypted_recipient=encrypt(f"{suffix}@example.test"), recipient_hash=suffix,
                    sent_at=timezone.now(),
                )
                policy = self._policy("new@example.test")
                policy = RenewalPolicy(**{**policy.__dict__, "remote_id": cycle.policy_remote_id})
                with patch("cotizacion_colectivos.services.renewals.list_collective_renewals", return_value=(policy,)):
                    sync_renewal_cycles(zoho=object(), today=date(2026, 8, 24))
                cycle.refresh_from_db()
                self.assertEqual(cycle.recipient_email, f"{suffix}@example.test")

    def test_upcoming_uses_programmed_status_not_legacy_selected_flag(self):
        programmed = RenovacionColectiva.objects.create(
            cycle_key="4991513000271000010:2026-09", policy_remote_id="4991513000271000010",
            policy_token="protected", masked_policy="0408", client_label="Empresa", branch_name="VG deudores",
            monthly_period="2026-09", scheduled_for=date(2026, 8, 31), selected=False,
            status=RenovacionColectiva.Status.PROGRAMMED,
        )
        sent = RenovacionColectiva.objects.create(
            cycle_key="4991513000271000011:2026-09", policy_remote_id="4991513000271000011",
            policy_token="protected", masked_policy="0409", client_label="Empresa", branch_name="VG deudores",
            monthly_period="2026-09", scheduled_for=date(2026, 8, 31), selected=False,
            status=RenovacionColectiva.Status.SENT,
        )
        rows = upcoming_cycles(today=date(2026, 8, 24))
        self.assertIn(programmed, rows)
        self.assertNotIn(sent, rows)
        self.assertEqual(tuple(tracking_cycles()), (sent,))

    def test_targeted_dry_run_only_counts_requested_cycle(self):
        target = RenovacionColectiva.objects.create(
            cycle_key="4991513000271000020:2026-09", policy_remote_id="4991513000271000020",
            policy_token="protected", masked_policy="0420", client_label="Empresa", branch_name="VG deudores", automation_eligible=True,
            monthly_period="2026-09", scheduled_for=date(2026, 12, 31), status=RenovacionColectiva.Status.PROGRAMMED,
        )
        other = RenovacionColectiva.objects.create(
            cycle_key="4991513000271000021:2026-09", policy_remote_id="4991513000271000021",
            policy_token="protected", masked_policy="0421", client_label="Otra", branch_name="VG deudores",
            monthly_period="2026-09", scheduled_for=date(2026, 8, 31), status=RenovacionColectiva.Status.PROGRAMMED,
        )
        result = process_renewal_cycles(now=timezone.now(), cycle_id=target.pk, force_due=True, dry_run=True)
        self.assertEqual(result["processed"], 1)
        self.assertEqual(result["sent"], 1)
        other.refresh_from_db()
        self.assertEqual(other.status, RenovacionColectiva.Status.PROGRAMMED)
        target.refresh_from_db()
        self.assertEqual(target.scheduled_for, date(2026, 12, 31))

    def test_force_due_requires_cycle_and_sandbox(self):
        with self.assertRaisesMessage(Exception, "--force-due requiere --cycle-id"):
            Command().handle(force_due=True, cycle_id=None, dry_run=True, limit=None, diagnose=False)
        with override_settings(ZOHO_ACTIVE_PROFILE="production"):
            with self.assertRaisesMessage(Exception, "sólo está permitido"):
                Command().handle(force_due=True, cycle_id=1, dry_run=True, limit=None, diagnose=False)

    def test_selection_is_local_and_defaults_unselected(self):
        cycle = RenovacionColectiva.objects.create(
            cycle_key="4991513000271000001:2026-09-10", policy_remote_id="4991513000271000001",
            policy_token="protected", masked_policy="0400", client_label="Empresa", branch_name="Salud colectivo",
            expiry_date=date(2026, 9, 10), monthly_period="2026-09", scheduled_for=date(2026, 8, 31),
        )
        self.assertFalse(cycle.selected)
        selected = set_renewal_selection(cycle_id=cycle.pk, selected=True, recipient="client@example.test")
        self.assertTrue(selected.selected)
        self.assertEqual(selected.status, RenovacionColectiva.Status.PROGRAMMED)
        self.assertEqual(upcoming_cycles(filter_name="programmed").count(), 1)

    def test_sent_cycle_moves_to_tracking_and_responded_is_not_resendable_by_default(self):
        cycle = RenovacionColectiva.objects.create(
            cycle_key="4991513000271000002:2026-09-10", policy_remote_id="4991513000271000002",
            policy_token="protected", masked_policy="0401", client_label="Empresa", branch_name="Vida grupo",
            expiry_date=date(2026, 9, 10), monthly_period="2026-09", scheduled_for=date(2026, 8, 31),
            status=RenovacionColectiva.Status.RESPONDED, selected=True, send_attempts=1,
        )
        self.assertEqual(tracking_cycles(status="RESPONDED").get(), cycle)
