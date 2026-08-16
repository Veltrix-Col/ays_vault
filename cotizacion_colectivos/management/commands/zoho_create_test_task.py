from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from integrations.zoho.exceptions import ZohoError

from cotizacion_colectivos.services.task_publisher import (
    TaskPublicationRejected,
    TaskPublicationUncertain,
    TaskPublishingDisabled,
    get_task_publisher,
)


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
        except (TaskPublishingDisabled, TaskPublicationRejected, ZohoError) as exc:
            category = getattr(exc, "category", "blocked")
            raise CommandError(f"No se creó la Task ({category}).") from exc

        self.stdout.write(self.style.SUCCESS("Task creada correctamente"))
        self.stdout.write(f"Profile: {result['profile']}")
        self.stdout.write(f"Module: {result['module']}")
        self.stdout.write(f"Record ID: {result['record_id']}")
