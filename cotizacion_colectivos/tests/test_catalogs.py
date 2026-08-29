from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from cotizacion_colectivos.forms import PersonCompletionForm
from cotizacion_colectivos.quotation_forms.catalog import get_branch_schema
from cotizacion_colectivos.quotation_forms.forms import IndividualQuotationForm
from cotizacion_colectivos.services.catalogs import CatalogUnavailable, get_identification_type_choices


class IdentificationCatalogTests(SimpleTestCase):
    def _field(self, options):
        return SimpleNamespace(api_name="Tipo_ID", pick_list_values=options)

    @patch("cotizacion_colectivos.services.catalogs.cached_metadata_fields")
    def test_uses_active_api_values_labels_and_sequence(self, metadata):
        metadata.return_value = (self._field((
            {"actual_value": "PAS", "display_value": "Pasaporte", "active": True, "sequence_number": 2},
            {"actual_value": "CC", "display_value": "Cédula", "active": True, "sequence_number": 1},
            {"actual_value": "OLD", "display_value": "Antiguo", "active": False, "sequence_number": 0},
        )),)
        choices = get_identification_type_choices(facade=object())
        self.assertEqual([(item.value, item.label) for item in choices], [("CC", "Cédula"), ("PAS", "Pasaporte")])

    @patch("cotizacion_colectivos.services.catalogs.cached_metadata_fields", return_value=())
    def test_missing_tipo_id_fails_without_invented_fallback(self, _metadata):
        with self.assertRaises(CatalogUnavailable):
            get_identification_type_choices(facade=object())

    def test_forms_use_api_value_and_display_label(self):
        choices = (("PAS", "Pasaporte"), ("CC", "Cédula"))
        person = PersonCompletionForm(identification_choices=choices)
        self.assertEqual(tuple(person.fields["id_type"].choices), (("", "Seleccione"), *choices))
        schema = get_branch_schema("vida")
        form = IndividualQuotationForm(schema=schema, identification_choices=choices, context={})
        self.assertEqual(tuple(form.fields["requester_id_type"].choices), (("", "Seleccione"), *choices))
