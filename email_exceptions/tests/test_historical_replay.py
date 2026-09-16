import csv
from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import SimpleTestCase

from email_exceptions.historical_replay import _historical_email, _text


class HistoricalReplayTests(SimpleTestCase):
    def test_adapter_maps_historical_row_without_persistence(self):
        email = _historical_email({
            "entry_id": "entry-1",
            "message_id": "message-1",
            "message_id_hash": "hash-1",
            "conversation_id": "conversation-1",
            "received_at": "2026-09-13T10:00:00Z",
            "from_name": "Remitente",
            "from_email": "sender@example.test",
            "subject_original": "Pago duplicado",
            "body_text": "Pago duplicado de póliza.",
            "has_attachments": "False",
        })
        self.assertEqual(email.external_message_id, "message-1")
        self.assertEqual(email.conversation_id, "conversation-1")
        self.assertEqual(email.subject, "Pago duplicado")
        self.assertIsNone(email.pk)

    def test_excel_text_truncation_does_not_change_structured_values(self):
        long_text = "x" * 40000
        self.assertEqual(len(_text(long_text)), 32001)
        self.assertEqual(_text("message-id"), "message-id")

    def test_replay_supports_a_csv_fixture_without_database_writes(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.csv"
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=("message_id", "subject_original", "body_text"))
                writer.writeheader()
                writer.writerow({"message_id": "m-1", "subject_original": "Comprobante", "body_text": "Pago"})
            rows = list(csv.DictReader(path.open(encoding="utf-8")))
            self.assertEqual(len(rows), 1)
            self.assertIsNone(_historical_email(rows[0]).pk)
