from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase


class MobilityContactSeedCommandTests(SimpleTestCase):
    def test_default_is_sanitized_dry_run_of_exactly_five_contacts(self):
        output = StringIO()
        with patch(
            "cotizacion_colectivos.management.commands.colectivos_seed_mobility_contacts.get_zoho"
        ) as get_zoho, patch(
            "cotizacion_colectivos.management.commands.colectivos_seed_mobility_contacts.GuardedSandboxContactPublisher.create"
        ) as create:
            call_command("colectivos_seed_mobility_contacts", stdout=output)
        text = output.getvalue()
        self.assertIn('"module": "Contacts"', text)
        self.assertIn('"planned": 5', text)
        self.assertIn('"writes": 0', text)
        self.assertEqual(text.count("VELTRIX TEST MOVILIDAD"), 5)
        self.assertNotIn("990000001001", text)
        get_zoho.assert_not_called()
        create.assert_not_called()

    def test_write_mode_remains_blocked_when_sandbox_write_is_disabled(self):
        with self.assertRaisesMessage(CommandError, "La escritura Sandbox está deshabilitada."):
            call_command(
                "colectivos_seed_mobility_contacts",
                confirm="SANDBOX_MOBILITY_CONTACT_SEED",
            )
