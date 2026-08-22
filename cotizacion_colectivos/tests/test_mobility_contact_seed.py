from io import StringIO

from django.core.management import call_command
from django.test import SimpleTestCase


class MobilityContactSeedCommandTests(SimpleTestCase):
    def test_default_is_sanitized_dry_run_of_exactly_five_contacts(self):
        output = StringIO()
        call_command("colectivos_seed_mobility_contacts", stdout=output)
        text = output.getvalue()
        self.assertIn('"module": "Contacts"', text)
        self.assertIn('"planned": 5', text)
        self.assertIn('"writes": 0', text)
        self.assertEqual(text.count("VELTRIX TEST MOVILIDAD"), 5)
        self.assertNotIn("990000001001", text)

