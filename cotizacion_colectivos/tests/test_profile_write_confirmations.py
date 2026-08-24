from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from cotizacion_colectivos.services.write_guards import require_write_guard


ENTITIES = ("task", "contact", "risk", "subrisk")


class ProfileWriteConfirmationTests(SimpleTestCase):
    @override_settings(
        ZOHO_ACTIVE_PROFILE="sandbox",
        COLECTIVOS_TASK_PUBLISH_ENABLED=True,
        COLECTIVOS_CONTACT_PUBLISH_ENABLED=True,
        COLECTIVOS_RISK_PUBLISH_ENABLED=True,
        COLECTIVOS_SUBRISK_PUBLISH_ENABLED=True,
        COLECTIVOS_SANDBOX_TASK_WRITE_CONFIRMATION="SANDBOX_TASK_WRITE",
        COLECTIVOS_SANDBOX_CONTACT_WRITE_CONFIRMATION="SANDBOX_CONTACT_WRITE",
        COLECTIVOS_SANDBOX_RISK_WRITE_CONFIRMATION="SANDBOX_RISK_WRITE",
        COLECTIVOS_SANDBOX_SUBRISK_WRITE_CONFIRMATION="SANDBOX_SUBRISK_WRITE",
    )
    def test_sandbox_accepts_only_sandbox_confirmations_for_all_entities(self):
        with patch(
            "cotizacion_colectivos.services.write_guards.ZohoSettings.from_django",
            return_value=SimpleNamespace(write_enabled=True),
        ):
            for entity, confirmation in (
                ("task", "SANDBOX_TASK_WRITE"),
                ("contact", "SANDBOX_CONTACT_WRITE"),
                ("risk", "SANDBOX_RISK_WRITE"),
                ("subrisk", "SANDBOX_SUBRISK_WRITE"),
            ):
                require_write_guard(
                    entity=entity, profile="sandbox", confirmation=confirmation,
                    feature_flag={
                        "task": "COLECTIVOS_TASK_PUBLISH_ENABLED",
                        "contact": "COLECTIVOS_CONTACT_PUBLISH_ENABLED",
                        "risk": "COLECTIVOS_RISK_PUBLISH_ENABLED",
                        "subrisk": "COLECTIVOS_SUBRISK_PUBLISH_ENABLED",
                    }[entity],
                    disabled_error=RuntimeError,
                )

    @override_settings(
        ZOHO_ACTIVE_PROFILE="sandbox",
        COLECTIVOS_SANDBOX_TASK_WRITE_CONFIRMATION="SANDBOX_TASK_WRITE",
        COLECTIVOS_SANDBOX_CONTACT_WRITE_CONFIRMATION="SANDBOX_CONTACT_WRITE",
        COLECTIVOS_SANDBOX_RISK_WRITE_CONFIRMATION="SANDBOX_RISK_WRITE",
        COLECTIVOS_SANDBOX_SUBRISK_WRITE_CONFIRMATION="SANDBOX_SUBRISK_WRITE",
    )
    def test_sandbox_rejects_production_confirmation(self):
        with patch(
            "cotizacion_colectivos.services.write_guards.ZohoSettings.from_django",
            return_value=SimpleNamespace(write_enabled=True),
        ):
            with self.assertRaises(RuntimeError):
                require_write_guard(
                    entity="task", profile="sandbox", confirmation="PRODUCTION_TASK_WRITE",
                    feature_flag="COLECTIVOS_TASK_PUBLISH_ENABLED",
                    disabled_error=RuntimeError,
                )

    @override_settings(
        ZOHO_ACTIVE_PROFILE="production",
        ZOHO_PRODUCTION_WRITE_ENABLED=False,
        COLECTIVOS_TASK_PUBLISH_ENABLED=True,
        COLECTIVOS_PRODUCTION_TASK_WRITE_CONFIRMATION="PRODUCTION_TASK_WRITE",
        COLECTIVOS_PRODUCTION_CONTACT_WRITE_CONFIRMATION="PRODUCTION_CONTACT_WRITE",
        COLECTIVOS_PRODUCTION_RISK_WRITE_CONFIRMATION="PRODUCTION_RISK_WRITE",
        COLECTIVOS_PRODUCTION_SUBRISK_WRITE_CONFIRMATION="PRODUCTION_SUBRISK_WRITE",
    )
    def test_production_write_false_blocks_all_entities(self):
        with patch(
            "cotizacion_colectivos.services.write_guards.ZohoSettings.from_django",
            return_value=SimpleNamespace(write_enabled=False),
        ):
            for entity, confirmation in (
                ("task", "PRODUCTION_TASK_WRITE"),
                ("contact", "PRODUCTION_CONTACT_WRITE"),
                ("risk", "PRODUCTION_RISK_WRITE"),
                ("subrisk", "PRODUCTION_SUBRISK_WRITE"),
                ):
                with self.assertRaises(RuntimeError):
                    require_write_guard(
                        entity=entity, profile="production", confirmation=confirmation,
                        feature_flag="COLECTIVOS_TASK_PUBLISH_ENABLED",
                        disabled_error=RuntimeError,
                    )

    @override_settings(
        ZOHO_ACTIVE_PROFILE="production",
        ZOHO_PRODUCTION_WRITE_ENABLED=True,
        COLECTIVOS_TASK_PUBLISH_ENABLED=True,
        COLECTIVOS_CONTACT_PUBLISH_ENABLED=True,
        COLECTIVOS_SUBRISK_PUBLISH_ENABLED=True,
        COLECTIVOS_RISK_PUBLISH_ENABLED=True,
        COLECTIVOS_PRODUCTION_TASK_WRITE_CONFIRMATION="PRODUCTION_TASK_WRITE",
        COLECTIVOS_PRODUCTION_CONTACT_WRITE_CONFIRMATION="PRODUCTION_CONTACT_WRITE",
        COLECTIVOS_PRODUCTION_RISK_WRITE_CONFIRMATION="PRODUCTION_RISK_WRITE",
        COLECTIVOS_PRODUCTION_SUBRISK_WRITE_CONFIRMATION="PRODUCTION_SUBRISK_WRITE",
    )
    def test_production_write_true_accepts_only_production_confirmation(self):
        with patch(
            "cotizacion_colectivos.services.write_guards.ZohoSettings.from_django",
            return_value=SimpleNamespace(write_enabled=True),
        ):
            for entity, confirmation, feature_flag in (
                ("task", "PRODUCTION_TASK_WRITE", "COLECTIVOS_TASK_PUBLISH_ENABLED"),
                ("contact", "PRODUCTION_CONTACT_WRITE", "COLECTIVOS_CONTACT_PUBLISH_ENABLED"),
                ("risk", "PRODUCTION_RISK_WRITE", "COLECTIVOS_RISK_PUBLISH_ENABLED"),
                ("subrisk", "PRODUCTION_SUBRISK_WRITE", "COLECTIVOS_SUBRISK_PUBLISH_ENABLED"),
            ):
                require_write_guard(
                    entity=entity, profile="production", confirmation=confirmation,
                    feature_flag=feature_flag, disabled_error=RuntimeError,
                )
            with self.assertRaises(RuntimeError):
                require_write_guard(
                    entity="task", profile="production", confirmation="SANDBOX_TASK_WRITE",
                    feature_flag="COLECTIVOS_TASK_PUBLISH_ENABLED",
                    disabled_error=RuntimeError,
                )
