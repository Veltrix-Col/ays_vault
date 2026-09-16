import tempfile
from pathlib import Path
from unittest.mock import patch

from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

from email_exceptions.database_safety import validate_isolated_database
from email_exceptions.management.commands.email_exceptions_backfill import (
    validate_isolated_database as validate_command_database,
)
from email_exceptions.persistent_benchmark import benchmark_backfill, validate_isolated_database as validate_benchmark_database


class PersistentBenchmarkDatabaseGuardTests(SimpleTestCase):
    def database_settings(self, default_name, isolated_name):
        return {
            "default": {"ENGINE": "django.db.backends.sqlite3", "NAME": str(default_name)},
            "isolated": {"ENGINE": "django.db.backends.sqlite3", "NAME": str(isolated_name)},
        }

    def test_default_database_is_rejected_before_backfill(self):
        with patch("email_exceptions.persistent_benchmark.backfill.run_backfill") as run_backfill:
            with self.assertRaisesMessage(ValueError, "BACKFILL_DATABASE_MUST_BE_ISOLATED"):
                benchmark_backfill("unused.csv", "unused.xlsx", database="default", offset=0, limit=1)
        run_backfill.assert_not_called()

    def test_different_sqlite_alias_to_same_physical_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "default.sqlite3"
            databases = self.database_settings(db_path, db_path.parent / "." / db_path.name)
            with override_settings(DATABASES=databases):
                with self.assertRaisesMessage(ValueError, "BACKFILL_DATABASE_MUST_BE_ISOLATED"):
                    benchmark_backfill("unused.csv", "unused.xlsx", database="isolated", offset=0, limit=1)

    def test_isolated_alias_is_allowed_and_persistent_call_is_forwarded(self):
        with tempfile.TemporaryDirectory() as directory:
            databases = self.database_settings(
                Path(directory) / "default.sqlite3",
                Path(directory) / "backfill.sqlite3",
            )
            with override_settings(DATABASES=databases):
                validate_isolated_database("isolated")
                with patch(
                    "email_exceptions.persistent_benchmark.backfill.run_backfill",
                    return_value={"summary": {"MESSAGES_PROCESSED": 0}},
                ) as run_backfill:
                    benchmark_backfill("dataset.csv", "report.xlsx", database="isolated", offset=10, limit=20)
            run_backfill.assert_called_once_with(
                "dataset.csv", "report.xlsx", database="isolated", offset=10, limit=20, dry_run=False
            )

    def test_command_and_benchmark_share_the_same_validation_policy(self):
        self.assertIs(validate_command_database.__globals__["_validate_isolated_database"], validate_benchmark_database)

    def test_same_postgresql_database_is_rejected_even_with_another_user(self):
        databases = {
            "default": {
                "ENGINE": "django.db.backends.postgresql", "NAME": "vault",
                "HOST": "db.internal", "PORT": "5432", "USER": "app_user",
            },
            "isolated": {
                "ENGINE": "django.db.backends.postgresql", "NAME": "vault",
                "HOST": "db.internal", "PORT": "5432", "USER": "other_user",
            },
        }
        with override_settings(DATABASES=databases):
            with self.assertRaisesMessage(ValueError, "BACKFILL_DATABASE_MUST_BE_ISOLATED"):
                validate_isolated_database("isolated")
