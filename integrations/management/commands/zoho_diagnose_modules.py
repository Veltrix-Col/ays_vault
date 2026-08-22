from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from integrations.management.commands.zoho_export_schema import _atomic_text
from integrations.zoho import get_zoho
from integrations.zoho.diagnostics import ModuleDiagnostic, ModuleDiagnosticsService
from integrations.zoho.exceptions import ZohoError
from integrations.zoho.records import validate_api_name


class Command(BaseCommand):
    help = "Diagnostica Fields API por módulo sin consultar registros."

    def add_arguments(self, parser):
        parser.add_argument("--profile", default="")
        parser.add_argument("--module", action="append", dest="modules", default=[])
        parser.add_argument("--output", default="")

    def handle(self, *args, **options):
        requested = tuple(
            validate_api_name(item.strip(), label="módulo")
            for item in options["modules"]
            if item.strip()
        )
        profile = options["profile"].strip() or None
        try:
            facade = get_zoho(profile=profile)
            organization = facade.organization.get()
            modules = facade.metadata.list_modules()
        except ZohoError as exc:
            raise CommandError(
                f"No fue posible consultar Modules API ({exc.category})."
            ) from exc
        self.stdout.write(f"Perfil: {facade.profile}")
        self.stdout.write(f"Entorno: {facade.environment}")
        self.stdout.write(f"Backend: {facade.backend_name.upper()}")
        self.stdout.write(
            f"Organización: {organization.company_name or 'disponible'}"
        )
        self.stdout.write("Modo: solo lectura")
        self.stdout.write(f"Resource path: {facade.config.sdk_resource_path}")
        self.stdout.write("")

        index = {item.api_name.casefold(): item for item in modules}
        selected = []
        for name in requested:
            module = index.get(name.casefold())
            if module is None:
                raise CommandError(
                    f"El módulo {name} no aparece en Modules API de Zoho."
                )
            selected.append(module)
        if not requested:
            selected = sorted(modules, key=lambda item: item.api_name.casefold())

        service = ModuleDiagnosticsService.build(
            profile=getattr(facade, "profile", profile)
        )
        results = [service.diagnose(module) for module in selected]
        for result in results:
            self._write_result(result)

        output = options["output"].strip()
        if output:
            target = Path(output).expanduser().resolve()
            payload = [self._json_result(item) for item in results]
            _atomic_text(
                target,
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            )
            self.stdout.write(f"Reporte seguro: {target}")

    def _write_result(self, result: ModuleDiagnostic) -> None:
        module = result.module
        error = result.preferred_error
        label = module.plural_label or module.module_name or module.api_name
        lines = [
            f"Módulo: {module.api_name}",
            "Visible en Modules API: Sí",
            f"Etiqueta: {label}",
            f"Tipo: {'personalizado' if module.custom_module else 'estándar'}",
            f"Estado: {module.status or 'no informado'}",
            f"API soportada: {'Sí' if module.api_supported else 'No'}",
            f"Generated type: {module.generated_type or 'no informado'}",
            f"Fields vía SDK: {_attempt(result.sdk_fields, result.sdk_error)}",
            f"Fields vía REST: {_attempt(result.rest_fields, result.rest_error)}",
        ]
        if error is not None:
            lines.extend(
                [
                    f"Backend del error: {error.backend.upper()}",
                    f"HTTP: {error.http_status or 'no informado'}",
                    f"Código Zoho: {error.zoho_code or 'no informado'}",
                    f"Estado Zoho: {error.zoho_status or 'no informado'}",
                    f"Mensaje seguro: {error.message}",
                    "Claves details: "
                    + (", ".join(error.detail_keys) or "ninguna"),
                    f"Request ID: {error.request_id or 'no informado'}",
                ]
            )
            if error.backend == "sdk":
                lines.extend(
                    [
                        f"Excepción SDK: {error.exception_class or 'no informada'}",
                        f"Código SDK: {error.sdk_code or 'no informado'}",
                        "Solicitud enviada: " + _request_sent(error.request_sent),
                    ]
                )
        lines.extend(
            [
                f"Clasificación: {result.classification.value}",
                f"Recomendación: {result.recommendation}",
                "",
            ]
        )
        self.stdout.write("\n".join(lines))

    @staticmethod
    def _json_result(result: ModuleDiagnostic) -> dict[str, object]:
        error = result.preferred_error
        return {
            "api_name": result.module.api_name,
            "etiqueta": (
                result.module.plural_label
                or result.module.module_name
                or result.module.api_name
            ),
            "estado": result.module.status,
            "backend_intentado": list(result.attempted_backends),
            "http_status": error.http_status if error else None,
            "codigo_zoho": error.zoho_code if error else "",
            "clasificacion": result.classification.value,
            "recomendacion": result.recommendation,
            "timestamp": datetime.now(UTC).isoformat(),
        }


def _attempt(fields: int | None, error) -> str:
    if fields is not None:
        return f"Sí ({fields} campos)" if fields else "Sin contenido"
    if error is not None:
        return "No"
    return "No intentado"


def _request_sent(value: bool | None) -> str:
    if value is True:
        return "Sí"
    if value is False:
        return "No"
    return "No determinable por el SDK"
