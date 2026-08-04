from __future__ import annotations

import re
import time
from dataclasses import dataclass
from types import SimpleNamespace

from django.core.management.base import BaseCommand, CommandError

from integrations.zoho import get_zoho
from integrations.zoho.exceptions import ZohoError

from cotizacion_colectivos.services.common import (
    ColectivosServiceError,
    escape_criteria_value,
    get_colectivos_profile,
)
from cotizacion_colectivos.services.mappings import (
    COMPANY_ID_TYPE,
    COMPANY_TYPE,
    CONTACT_SEARCH_FIELDS,
    CONTACTS_MODULE,
    SEARCH_LIMIT,
)
from cotizacion_colectivos.services.search import CompanySearchService, _fixed_criteria


PRODUCTION_PROFILE = "production"
SAFE_DOCUMENT = re.compile(r"^[0-9 .-]{3,50}$")
DIAGNOSTIC_FIELDS = (
    "id",
    "N_mero_de_ID",
    "Tipo_ID",
    "Tipo_de_persona",
    "Layout",
)
METADATA_FIELDS = (
    "N_mero_de_ID",
    "Tipo_ID",
    "Tipo_de_persona",
    "Nombre_comercial",
    "Raz_n_social",
    "Layout",
)


@dataclass(frozen=True)
class DiagnosticResult:
    key: str
    filters: str
    records: tuple[dict[str, object], ...]
    duration_ms: int
    backend: str
    error: str = "none"

    @property
    def ids(self) -> frozenset[str]:
        return frozenset(
            str(record.get("id") or "")
            for record in self.records
            if record.get("id")
        )


class _TimedSearch:
    def __init__(self, delegate) -> None:
        self._delegate = delegate
        self.calls: list[int] = []

    def by_criteria(self, **kwargs):
        started = time.perf_counter()
        try:
            return self._delegate.by_criteria(**kwargs)
        finally:
            self.calls.append(_elapsed_ms(started))


class Command(BaseCommand):
    help = (
        "Diagnostica de forma cerrada una búsqueda de empresa en Contacts de "
        "Producción, sin imprimir valores del registro."
    )

    def add_arguments(self, parser):
        parser.add_argument("--profile", required=True)
        parser.add_argument("--document", required=True)
        parser.add_argument("--allow-production-read", action="store_true")

    def handle(self, *args, **options):
        profile = str(options["profile"] or "").strip().lower()
        if profile != PRODUCTION_PROFILE:
            raise CommandError("Este diagnóstico admite exclusivamente --profile production.")
        if not options["allow_production_read"]:
            raise CommandError(
                "Debe confirmar la lectura con --allow-production-read."
            )
        document = str(options["document"] or "").strip()
        if not SAFE_DOCUMENT.fullmatch(document):
            raise CommandError("El documento local no tiene un formato permitido.")
        if get_colectivos_profile() != PRODUCTION_PROFILE:
            raise CommandError(
                "ZOHO_ACTIVE_PROFILE debe ser production para reproducir el servicio real."
            )

        total_started = time.perf_counter()
        facade_started = time.perf_counter()
        try:
            zoho = get_zoho(profile=PRODUCTION_PROFILE)
        except ZohoError as exc:
            raise CommandError(
                f"No fue posible preparar Producción ({exc.category})."
            ) from exc
        facade_ms = _elapsed_ms(facade_started)
        if zoho.profile != PRODUCTION_PROFILE or zoho.environment != PRODUCTION_PROFILE:
            raise CommandError("La fachada Zoho no corresponde a Producción.")

        organization_started = time.perf_counter()
        try:
            organization = zoho.organization.get()
        except ZohoError as exc:
            raise CommandError(
                f"No fue posible validar Producción ({exc.category})."
            ) from exc
        organization_ms = _elapsed_ms(organization_started)
        if organization.environment != PRODUCTION_PROFILE:
            raise CommandError("Zoho no confirmó el entorno Production solicitado.")

        metadata_started = time.perf_counter()
        try:
            metadata = zoho.metadata.list_fields(CONTACTS_MODULE)
        except ZohoError as exc:
            raise CommandError(
                f"No fue posible consultar Fields API ({exc.category})."
            ) from exc
        metadata_ms = _elapsed_ms(metadata_started)
        metadata_index = {field.api_name: field for field in metadata}

        escaped = escape_criteria_value(document)
        number = f"(N_mero_de_ID:equals:{escaped})"
        criteria = (
            ("A", "Número ID exacto", number),
            (
                "B",
                "Número ID exacto + Tipo ID",
                f"({number}and(Tipo_ID:equals:{COMPANY_ID_TYPE}))",
            ),
            (
                "C",
                "Número ID exacto + Tipo de persona",
                f"({number}and(Tipo_de_persona:equals:{COMPANY_TYPE}))",
            ),
            (
                "D",
                "Número ID exacto + Tipo ID + Tipo de persona",
                f"({number}and(Tipo_ID:equals:{COMPANY_ID_TYPE})and"
                f"(Tipo_de_persona:equals:{COMPANY_TYPE}))",
            ),
            (
                "E",
                "Consulta exacta actual del servicio",
                _fixed_criteria(
                    COMPANY_TYPE,
                    COMPANY_ID_TYPE,
                    number,
                    include_id_type=False,
                ),
            ),
        )

        results = tuple(
            self._search(zoho, key=key, filters=filters, criteria=value)
            for key, filters, value in criteria
        )

        timed_search = _TimedSearch(zoho.search)
        service_facade = SimpleNamespace(search=timed_search)
        service_started = time.perf_counter()
        service_error = "none"
        service_results = ()
        try:
            service_results = CompanySearchService(service_facade).search(document)
        except ColectivosServiceError as exc:
            service_error = exc.code
        except ZohoError as exc:
            service_error = exc.category
        service_ms = _elapsed_ms(service_started)
        service_dedup_ms = max(0, service_ms - sum(timed_search.calls))

        self.stdout.write("Diagnóstico seguro de búsqueda de empresa en Production:")
        self.stdout.write("- Módulo: Contacts")
        self.stdout.write(f"- Backend: {str(zoho.backend_name).upper()}")
        self.stdout.write("- Modo: solo lectura")
        self.stdout.write(f"- Inicialización/fachada: {facade_ms} ms")
        self.stdout.write(f"- Validación Organization: {organization_ms} ms")
        self.stdout.write(f"- Metadata Fields: {metadata_ms} ms")
        for result in results:
            self._write_result(result)
        self.stdout.write(
            "F. Servicio completo | "
            f"encontrado={'sí' if service_results else 'no'} | "
            f"cantidad={len(service_results)} | duración={service_ms} ms | "
            "filtros=flujo numérico actual (exacto y prefijo) | "
            f"backend={str(zoho.backend_name).upper()} | error={service_error}"
        )
        self.stdout.write(
            f"- Search API del servicio: {len(timed_search.calls)} llamada(s), "
            f"{sum(timed_search.calls)} ms"
        )
        self.stdout.write(f"- Deduplicación y mapeo del servicio: {service_dedup_ms} ms")
        self.stdout.write(
            f"- Search API total: {len(results) + len(timed_search.calls)} llamada(s)"
        )
        self.stdout.write(f"- Duración total: {_elapsed_ms(total_started)} ms")
        self.stdout.write(f"- Filtro que elimina: {_eliminating_filter(results)}")
        self._write_observed_values(results[0], metadata_index)
        self._write_metadata(metadata_index)
        self.stdout.write(
            "No se imprimieron valores sensibles, no se almacenaron respuestas y no se escribió en Zoho."
        )

    @staticmethod
    def _search(zoho, *, key: str, filters: str, criteria: str) -> DiagnosticResult:
        started = time.perf_counter()
        try:
            page = zoho.search.by_criteria(
                module=CONTACTS_MODULE,
                criteria=criteria,
                fields=DIAGNOSTIC_FIELDS,
                page=1,
                limit=SEARCH_LIMIT,
            )
            records = tuple(page.records[:SEARCH_LIMIT])
            error = "none"
        except ZohoError as exc:
            records = ()
            error = exc.category
        return DiagnosticResult(
            key=key,
            filters=filters,
            records=records,
            duration_ms=_elapsed_ms(started),
            backend=str(zoho.backend_name).upper(),
            error=error,
        )

    def _write_result(self, result: DiagnosticResult) -> None:
        self.stdout.write(
            f"{result.key}. {result.filters} | "
            f"encontrado={'sí' if result.records else 'no'} | "
            f"cantidad={len(result.records)} | duración={result.duration_ms} ms | "
            f"filtros={result.filters} | backend={result.backend} | error={result.error}"
        )

    def _write_observed_values(self, result, metadata_index) -> None:
        if not result.records:
            self.stdout.write("Valores categóricos observados: no disponibles; A no encontró registros.")
            return
        id_types = {_safe_category(record.get("Tipo_ID"), {COMPANY_ID_TYPE}) for record in result.records}
        person_types = {
            _safe_category(record.get("Tipo_de_persona"), {COMPANY_TYPE})
            for record in result.records
        }
        layouts = {_safe_layout(record.get("Layout")) for record in result.records}
        self.stdout.write("Valores categóricos observados en A:")
        self.stdout.write(
            f"- N_mero_de_ID: API name confirmado; tipo={_field_type(metadata_index, 'N_mero_de_ID')}"
        )
        self.stdout.write(
            f"- Tipo_ID: API name confirmado; valor={_joined(id_types)}; "
            f"tipo={_field_type(metadata_index, 'Tipo_ID')}"
        )
        self.stdout.write(
            f"- Tipo_de_persona: API name confirmado; valor={_joined(person_types)}; "
            f"tipo={_field_type(metadata_index, 'Tipo_de_persona')}"
        )
        self.stdout.write(f"- Layout: {_joined(layouts)}")

    def _write_metadata(self, metadata_index) -> None:
        self.stdout.write("Comparación de mappings con Fields API:")
        for api_name in METADATA_FIELDS:
            field = metadata_index.get(api_name)
            if field is None:
                self.stdout.write(f"- {api_name}: no aparece en metadata")
                continue
            picklists = _safe_picklists(field, api_name)
            searchable = _flag(getattr(field, "searchable", None))
            readable = _readable(field)
            self.stdout.write(
                f"- {api_name}: label={field.field_label}; tipo={field.data_type}; "
                f"searchable={searchable}; readable={readable}; picklist={picklists}"
            )


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))


def _eliminating_filter(results: tuple[DiagnosticResult, ...]) -> str:
    indexed = {result.key: result for result in results}
    a_ids = indexed["A"].ids
    if not a_ids:
        return "Número ID exacto o campo/API name; A no encontró registros"
    if not (a_ids & indexed["B"].ids):
        return "Tipo_ID=NIT"
    if not (a_ids & indexed["C"].ids):
        return "Tipo_de_persona=Persona jurídica"
    if not (a_ids & indexed["D"].ids):
        return "combinación Tipo_ID + Tipo_de_persona"
    if indexed["D"].ids != indexed["E"].ids:
        return "diferencia de agrupación entre D y el criterio actual"
    return "ninguno de los filtros exactos; revisar flujo de servicio/prefijo"


def _safe_category(value: object, allowed: set[str]) -> str:
    clean = str(value or "").strip()
    if not clean:
        return "vacío"
    return clean if clean in allowed else "otro valor categórico"


def _safe_layout(value: object) -> str:
    if not isinstance(value, dict):
        return "vacío" if not value else "estructura no reconocida"
    name = str(value.get("name") or "").strip()
    return name if name else "lookup sin nombre"


def _joined(values: set[str]) -> str:
    return ", ".join(sorted(values)) or "no informado"


def _field_type(metadata_index, api_name: str) -> str:
    field = metadata_index.get(api_name)
    return str(getattr(field, "data_type", "") or "no informado")


def _safe_picklists(field, api_name: str) -> str:
    if api_name not in {"Tipo_ID", "Tipo_de_persona"}:
        return "no aplica"
    allowed = {COMPANY_ID_TYPE} if api_name == "Tipo_ID" else {COMPANY_TYPE}
    values = set()
    for item in getattr(field, "pick_list_values", ()) or ():
        if not isinstance(item, dict):
            continue
        value = str(item.get("actual_value") or item.get("display_value") or "").strip()
        if value in allowed:
            values.add(value)
    return _joined(values) if values else "valor esperado no informado"


def _flag(value: object) -> str:
    if value is True:
        return "sí"
    if value is False:
        return "no"
    return "no informado por el DTO"


def _readable(field) -> str:
    for attribute in ("api_read", "readable"):
        value = getattr(field, attribute, None)
        if value is not None:
            return _flag(value)
    return "sí (presente en Fields API)"
