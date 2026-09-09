from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from integrations.zoho import get_zoho
from integrations.zoho.exceptions import ZohoError

from cotizacion_colectivos.excepciones_facturacion.application import PRODUCTION_WRITE_FLAGS
from cotizacion_colectivos.task_production_audit import (
    audit_tasks_contract,
    render_tasks_contract_markdown,
)


def _enabled(name: str) -> bool:
    value = getattr(settings, name, False)
    return value.strip().casefold() in {"1", "true", "yes", "on"} if isinstance(value, str) else bool(value)


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


class Command(BaseCommand):
    help = "Audita metadata y una muestra sanitizada de Tasks en Production (READ-only)."

    def add_arguments(self, parser):
        parser.add_argument("--profile", required=True, choices=("production",))
        parser.add_argument("--allow-production-read", action="store_true")
        parser.add_argument("--output-dir", default="artifacts/zoho/tasks-production")

    def handle(self, *args, **options):
        if not options["allow_production_read"]:
            raise CommandError("Debe confirmar Production READ-only con --allow-production-read.")
        enabled = [name for name in PRODUCTION_WRITE_FLAGS if _enabled(name)]
        if enabled:
            raise CommandError("Todos los guards WRITE/PUBLISH deben estar deshabilitados: " + ", ".join(enabled))
        try:
            zoho = get_zoho(profile="production", backend="rest")
            contract = audit_tasks_contract(zoho)
        except (ZohoError, ValueError) as exc:
            category = getattr(exc, "category", "contract")
            raise CommandError(f"No fue posible auditar Tasks Production ({category}).") from exc
        target = Path(options["output_dir"]).resolve()
        _atomic_write(
            target / "tasks_contract.json",
            json.dumps(contract, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
        _atomic_write(target / "tasks_contract.md", render_tasks_contract_markdown(contract))
        self.stdout.write(self.style.SUCCESS("Auditoría Tasks Production READ-only completada."))
        self.stdout.write(f"Fields: {contract['field_count']}")
        self.stdout.write(f"Production READ: {contract['audit']['production_reads']}")
        self.stdout.write("Production WRITE: 0")
        self.stdout.write(str(target))
