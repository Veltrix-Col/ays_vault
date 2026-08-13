from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.http import Http404
from django.test import RequestFactory, TestCase, override_settings

from cotizacion_colectivos.modes import (
    INVITATIONS_MODE,
    REQUESTS_MODE,
    SESSION_KEY,
    resolve_tool_mode,
)
from cotizacion_colectivos.services.search import UnifiedClientSearchService


class ToolModeTests(TestCase):
    def request(self):
        request = RequestFactory().get("/")
        request.session = {}
        return request

    def test_mode_is_allowlisted_and_persisted_as_visual_context_only(self):
        request = self.request()
        self.assertEqual(resolve_tool_mode(request).code, REQUESTS_MODE)
        self.assertEqual(resolve_tool_mode(request, INVITATIONS_MODE).code, INVITATIONS_MODE)
        self.assertEqual(request.session[SESSION_KEY], INVITATIONS_MODE)
        self.assertEqual(resolve_tool_mode(request).code, INVITATIONS_MODE)

    def test_unknown_mode_is_rejected(self):
        request = self.request()
        with self.assertRaisesMessage(Http404, "Herramienta no disponible"):
            resolve_tool_mode(request, "production")


@override_settings(ZOHO_ACTIVE_PROFILE="sandbox")
class UnifiedClientSearchTests(TestCase):
    @patch("cotizacion_colectivos.services.search.colectivos_zoho")
    def test_company_and_person_search_share_one_validated_facade(self, colectivos_zoho):
        facade = Mock()
        colectivos_zoho.return_value = facade

        def results(**kwargs):
            if "Persona jurídica" in kwargs["criteria"]:
                records = ({
                    "id": "5234567890123456789",
                    "Tipo_de_persona": "Persona jurídica",
                    "Tipo_ID": "NIT",
                    "N_mero_de_ID": "900000001",
                    "Nombre_comercial": "Cliente Empresa",
                    "Estado": "Cliente",
                },)
            else:
                records = ({
                    "id": "6234567890123456789",
                    "Tipo_de_persona": "Persona natural",
                    "Tipo_ID": "CC",
                    "N_mero_de_ID": "100000001",
                    "Full_Name": "Cliente Persona",
                    "Estado": "Cliente",
                },)
            return SimpleNamespace(records=records)

        facade.search.by_criteria.side_effect = results
        service = UnifiedClientSearchService()
        found = service.search("Cliente")

        self.assertEqual([item.source_kind for item in found], ["company", "person"])
        self.assertEqual([item.entity_label for item in found], ["Empresa", "Persona"])
        colectivos_zoho.assert_called_once()
        self.assertIs(service.company_service.zoho, service.person_service.zoho)
        self.assertEqual(facade.search.by_criteria.call_count, 2)

    @patch("cotizacion_colectivos.services.search.colectivos_zoho")
    def test_global_limit_is_applied_after_resolving_both_entity_types(self, colectivos_zoho):
        facade = Mock()
        colectivos_zoho.return_value = facade
        def results(**kwargs):
            person = "Persona natural" in kwargs["criteria"]
            return SimpleNamespace(records=tuple({
                "id": f"{'6' if person else '5'}2345678901234567{index:02d}",
                "Tipo_de_persona": "Persona natural" if person else "Persona jurídica",
                "Tipo_ID": "CC" if person else "NIT",
                "N_mero_de_ID": f"{'100' if person else '900'}000{index:03d}",
                "Full_Name": f"Persona {index}",
                "Nombre_comercial": f"Empresa {index}",
                "Estado": "Cliente",
            } for index in range(20)))
        facade.search.by_criteria.side_effect = results

        found = UnifiedClientSearchService().search("Cliente")

        self.assertEqual(len(found), 20)
        self.assertEqual(facade.search.by_criteria.call_count, 2)
