from __future__ import annotations

import re

from django.core.management.base import BaseCommand, CommandError

from integrations.zoho.exceptions import ZohoError

from cotizacion_colectivos.services.task_publisher import (
    TaskPublicationRejected,
    TaskPublicationUncertain,
    TaskPublishingDisabled,
    get_task_publisher,
)


SAFE_DIAGNOSTIC_VALUE = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")


def _safe_value(value) -> str:
    candidate = str(value or "").strip()
    return candidate if SAFE_DIAGNOSTIC_VALUE.fullmatch(candidate) else "unknown"


def _safe_status_code(value) -> str:
    return str(value) if isinstance(value, int) and not isinstance(value, bool) else "unknown"


def _request_sent(value) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    return "unknown"


def _safe_detail_keys(value) -> str:
    keys = []
    for item in tuple(value or ())[:12]:
        candidate = _safe_value(item)
        if candidate != "unknown":
            keys.append(candidate)
    return f"[{', '.join(keys)}]" if keys else "none"


def _safe_zoho_diagnostic(exc: ZohoError) -> str:
    values = (
        ("category", _safe_value(getattr(exc, "category", ""))),
        ("status_code", _safe_status_code(getattr(exc, "status_code", None))),
        ("backend", _safe_value(getattr(exc, "backend", ""))),
        ("operation", _safe_value(getattr(exc, "operation", ""))),
        ("module", _safe_value(getattr(exc, "module", ""))),
        ("sdk_exception_class", _safe_value(getattr(exc, "sdk_exception_class", ""))),
        ("sdk_code", _safe_value(getattr(exc, "sdk_code", ""))),
        ("zoho_code", _safe_value(getattr(exc, "zoho_code", ""))),
        ("zoho_status", _safe_value(getattr(exc, "zoho_status", ""))),
        ("detail_keys", _safe_detail_keys(getattr(exc, "detail_keys", ()))),
        ("request_sent", _request_sent(getattr(exc, "request_sent", None))),
    )
    return "No se creó la Task (" + "; ".join(
        f"{name}={value}" for name, value in values
    ) + ")."


class Command(BaseCommand):
    help = "Crea exactamente una Task sintética mediante la fachada Zoho en Sandbox."

    def add_arguments(self, parser):
        parser.add_argument("--profile", required=True)
        parser.add_argument("--confirm", default="")

    def handle(self, *args, **options):
        profile = str(options["profile"] or "").strip().lower()
        confirmation = str(options["confirm"] or "").strip()
        if profile != "sandbox":
            raise CommandError("Este comando admite exclusivamente --profile sandbox.")
        if not confirmation:
            raise CommandError("Debe proporcionar la confirmación explícita con --confirm.")

        try:
            publisher = get_task_publisher(
                profile=profile,
                confirmation=confirmation,
            )
            result = publisher.publish_test_task()
        except TaskPublicationUncertain as exc:
            raise CommandError(str(exc)) from exc
        except ZohoError as exc:
            raise CommandError(_safe_zoho_diagnostic(exc)) from exc
        except (TaskPublishingDisabled, TaskPublicationRejected) as exc:
            category = getattr(exc, "category", "blocked")
            raise CommandError(f"No se creó la Task ({category}).") from exc

        self.stdout.write(self.style.SUCCESS("Task creada correctamente"))
        self.stdout.write(f"Profile: {result['profile']}")
        self.stdout.write(f"Module: {result['module']}")
        self.stdout.write(f"Record ID: {result['record_id']}")
