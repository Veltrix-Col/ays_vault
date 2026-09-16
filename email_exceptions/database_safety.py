"""Safety checks shared by every persistent email-exceptions backfill path."""

from pathlib import Path

from django.conf import settings


def _database_identity(config):
    engine = str(config.get("ENGINE") or "").strip().lower()
    name = str(config.get("NAME") or "").strip()
    if engine.endswith("sqlite3"):
        return engine, str(Path(name).expanduser().resolve())
    return (
        engine,
        name,
        str(config.get("HOST") or "").strip().lower(),
        str(config.get("PORT") or "").strip(),
    )


def validate_isolated_database(alias, *, databases=None):
    """Reject unknown, default, or physically default-equivalent DB aliases."""
    configured = settings.DATABASES if databases is None else databases
    if alias not in configured:
        raise ValueError("BACKFILL_DATABASE_ALIAS_UNKNOWN")
    if alias == "default":
        raise ValueError("BACKFILL_DATABASE_MUST_BE_ISOLATED")
    if _database_identity(configured[alias]) == _database_identity(configured["default"]):
        raise ValueError("BACKFILL_DATABASE_MUST_BE_ISOLATED")
