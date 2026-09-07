from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from cotizacion_colectivos.forms import PersonCompletionForm
from cotizacion_colectivos.quotation_forms.catalog import get_branch_schema, with_relationship_choices
from cotizacion_colectivos.quotation_forms.forms import IndividualQuotationForm
from cotizacion_colectivos.services.catalogs import (
    CatalogUnavailable,
    get_identification_type_choices,
    get_subrisk_relationship_choices,
)


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
        self.assertEqual(
            [(item.value, item.label) for item in choices],
            [("CC", "CC - Cédula de ciudadanía"), ("PAS", "PAS - Pasaporte")],
        )

    @patch("cotizacion_colectivos.services.catalogs.cached_metadata_fields")
    def test_unknown_active_code_keeps_raw_value_and_label(self, metadata):
        metadata.return_value = (self._field((
            {"actual_value": "XYZ", "display_value": "Código interno", "active": True},
        )),)
        choice = get_identification_type_choices(facade=object())[0]
        self.assertEqual(choice.value, "XYZ")
        self.assertEqual(choice.label, "XYZ")

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

    @patch("cotizacion_colectivos.services.catalogs.cached_metadata_fields")
    def test_subrisk_relationship_catalog_uses_active_values_and_labels(self, metadata):
        metadata.return_value = (SimpleNamespace(api_name="Parentesco", pick_list_values=(
            {"actual_value": "Hijo", "display_value": "Hijo/a", "active": True, "sequence_number": 2},
            {"actual_value": "Afiliado", "display_value": "Afiliado", "active": True, "sequence_number": 1},
            {"actual_value": "OLD", "display_value": "Antiguo", "active": False},
            {"actual_value": None, "display_value": None, "active": True},
        )),)
        choices = get_subrisk_relationship_choices(facade=object())
        self.assertEqual([(item.value, item.label) for item in choices], [("Afiliado", "Afiliado"), ("Hijo", "Hijo/a")])

    @patch("cotizacion_colectivos.services.catalogs.cached_metadata_fields", return_value=())
    def test_subrisk_relationship_catalog_fails_closed(self, _metadata):
        with self.assertRaises(CatalogUnavailable):
            get_subrisk_relationship_choices(facade=object())

    def test_ci_relationship_field_uses_canonical_value_and_display_label(self):
        schema = with_relationship_choices(get_branch_schema("vida"), (("Hijo", "Hijo/a"),))
        relationship = next(field for group in schema.repeatables for field in group.fields if field.key == "relationship")
        self.assertEqual(relationship.kind, "choice")
        self.assertEqual(relationship.choices, (("Hijo", "Hijo/a"),))
