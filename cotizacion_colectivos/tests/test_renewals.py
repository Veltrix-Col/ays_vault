from datetime import date
from types import SimpleNamespace

from django.test import SimpleTestCase, TestCase, override_settings

from cotizacion_colectivos.models import RenovacionColectiva
from cotizacion_colectivos.services.renewals import diagnose_renewal_source, list_collective_renewals, _map_record, upcoming_cycles, tracking_cycles, set_renewal_selection


class RenewalReadContractTests(SimpleTestCase):
    def test_only_collective_policies_inside_thirty_day_window_are_selected(self):
        included = _map_record({"id": "4991513000271000001", "Name": "0400", "Ramo": "Salud colectivo", "L_nea_de_negocio": "Colectivo", "P_liza_Fecha_fin_de_la_vigencia": "2026-09-10", "Tomador_principal1": "Empresa"}, today=date(2026, 8, 24), window=30)
        self.assertIsNotNone(included)
        self.assertEqual(included.scheduled_for, date(2026, 8, 11))
        self.assertIsNone(_map_record({"id": "1", "L_nea_de_negocio": "Individual", "P_liza_Fecha_fin_de_la_vigencia": "2026-09-10"}, today=date(2026, 8, 24), window=30))
        self.assertIsNone(_map_record({"id": "2", "L_nea_de_negocio": "Colectivo", "P_liza_Fecha_fin_de_la_vigencia": "2026-10-01"}, today=date(2026, 8, 24), window=30))

    def test_list_uses_server_side_collective_criteria(self):
        class Search:
            def __init__(self): self.criteria = None
            def by_criteria(self, **kwargs):
                self.criteria = kwargs["criteria"]
                return SimpleNamespace(records=[{"id": "4991513000271000001", "Name": "0400", "L_nea_de_negocio": "Colectivo", "P_liza_Fecha_fin_de_la_vigencia": "2026-09-10"}], more_records=False)
        search = Search()
        fake = SimpleNamespace(search=search)
        rows = list_collective_renewals(zoho=fake, today=date(2026, 8, 24))
        self.assertEqual(len(rows), 1)
        self.assertIn("L_nea_de_negocio:equals:Colectivo", search.criteria)

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
                    "L_nea_de_negocio": value,
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
                    "id": "4991513000271000003", "Name": "0402", "Ramo": "Salud colectivo",
                    "L_nea_de_negocio": "Colectivo",
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
    def test_selection_is_local_and_defaults_unselected(self):
        cycle = RenovacionColectiva.objects.create(
            cycle_key="4991513000271000001:2026-09-10", policy_remote_id="4991513000271000001",
            policy_token="protected", masked_policy="0400", client_label="Empresa", branch_name="Salud colectivo",
            expiry_date=date(2026, 9, 10), scheduled_for=date(2026, 8, 11),
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
            expiry_date=date(2026, 9, 10), scheduled_for=date(2026, 8, 11),
            status=RenovacionColectiva.Status.RESPONDED, selected=True, send_attempts=1,
        )
        self.assertEqual(tracking_cycles(status="RESPONDED").get(), cycle)
