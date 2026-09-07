"""Safe, reproducible settings profile for local/CI Django tests.

The base settings remain the source of truth.  Environment defaults are set
before importing them so this profile also works for ``manage.py check`` (not
only for the test runner, which normally sets ``RUNNING_TESTS`` itself).
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path


os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DEBUG", "true")
os.environ.setdefault("SECRET_KEY", "local-test-only-not-for-production")
os.environ.setdefault("FIELD_ENCRYPTION_KEY", "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=")
os.environ.setdefault("FIELD_FINGERPRINT_KEY", "local-test-fingerprint-key")
os.environ.setdefault("DB_ENGINE", "sqlite")
os.environ.setdefault("ZOHO_ENABLED", "false")
os.environ.setdefault("ZOHO_PRODUCTION_WRITE_ENABLED", "false")
os.environ.setdefault("COLECTIVOS_INTERNAL_PUBLIC_ACCESS", "false")

from .settings import *  # noqa: F401,F403,E402


TEST_ROOT = Path(tempfile.gettempdir()) / "ays_tc_vault_django_tests"
TEST_ROOT.mkdir(parents=True, exist_ok=True)

DEBUG = True
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
        "TEST": {"NAME": ":memory:"},
        "OPTIONS": {"timeout": 20},
    }
}
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
ALERT_EMAIL_BACKEND = "console"
COLECTIVOS_PRIVATE_ROOT = TEST_ROOT / "private_assets" / "colectivos"
COLECTIVOS_PRIVATE_ROOT.mkdir(parents=True, exist_ok=True)
STATIC_ROOT = TEST_ROOT / "staticfiles"

# A test profile must never perform external writes, even if a developer's
# shell has exported a permissive value.
ZOHO_PRODUCTION_WRITE_ENABLED = False
ZOHO_SANDBOX_WRITE_ENABLED = False
COLECTIVOS_TASK_PUBLISH_ENABLED = False
COLECTIVOS_CONTACT_PUBLISH_ENABLED = False
COLECTIVOS_RISK_PUBLISH_ENABLED = False
COLECTIVOS_SUBRISK_PUBLISH_ENABLED = False
COLECTIVOS_ATTACHMENT_PUBLISH_ENABLED = False
COLECTIVOS_INVITATION_ATTACHMENT_PUBLISH_ENABLED = False
# Existing Django request tests exercise the local internal tool with an
# anonymous test client.  This bypass is confined to ``settings_test``;
# production settings and the versioned environment example remain closed.
COLECTIVOS_INTERNAL_PUBLIC_ACCESS = True
COLECTIVOS_DEADLINE_EMAIL_ENABLED = False
