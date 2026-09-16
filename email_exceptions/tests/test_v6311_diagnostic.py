from django.test import SimpleTestCase

from email_exceptions.v6311_diagnostic import _diagnostic_row, _sample


class V6311DiagnosticTests(SimpleTestCase):
    def _row(self, queue_result="POTENTIAL_FALSE_POSITIVE"):
        return {
            "message_id_hash": "hash-1",
            "subject": "Firma rechazada",
            "body_text": "La firma fue rechazada.",
            "RULE_ID": "SIGNATURE_FAILURE_V631",
            "FAMILY": "FIRMA",
            "EVENT_TYPE": "FIRMA_RECHAZADA",
            "ACTION_TYPE": "FOLLOW_UP",
            "SCOPE": "CASE",
            "MESSAGE_OUTCOME": "FAILURE",
            "ACTION_REQUIRED": True,
            "CURRENT_QUEUE": queue_result == "POTENTIAL_FALSE_POSITIVE",
            "QUEUE_RESULT": queue_result,
        }

    def test_diagnostic_row_assigns_root_cause_without_mutating_input(self):
        row = self._row()
        reference = {
            "V631_SHOULD_BE_IN_EXCEPTION_QUEUE": False,
            "V631_MESSAGE_OUTCOME": "INFORMATIONAL",
            "V631_SCOPE": "CASE",
        }
        result = _diagnostic_row(row, reference)
        self.assertEqual(result["DIVERGENCE_TYPE"], "FALSE_POSITIVE")
        self.assertEqual(result["ROOT_CAUSE"], "RULE_TOO_BROAD")
        self.assertEqual(row["RULE_ID"], "SIGNATURE_FAILURE_V631")

    def test_human_sample_has_empty_review_columns(self):
        sample = _sample([_diagnostic_row(self._row(), {"V631_SCOPE": "CASE"})])
        self.assertEqual(len(sample), 1)
        self.assertEqual(sample[0]["VALIDACION_HUMANA"], "")
        self.assertEqual(sample[0]["DECISION_HUMANA"], "")
        self.assertEqual(sample[0]["COMENTARIO_HUMANO"], "")
