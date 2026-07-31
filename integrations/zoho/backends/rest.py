from __future__ import annotations

import re
from collections.abc import Iterable

from ..coql import CoqlService
from ..constants import API_VERSION, MAX_FIELDS_PER_REQUEST, MAX_RECORDS_PER_REQUEST
from ..exceptions import ZohoInvalidResponseError, ZohoValidationError
from ..metadata import MetadataService
from ..pagination import page_from_response
from ..records import RecordsService, validate_api_name
from ..schemas import FieldMetadata, ModuleMetadata, Organization, Page
from ..services import ZohoServices
from ..settings import ZohoSettings

RECORD_ID = re.compile(r"^[A-Za-z0-9_-]{1,100}$")


class RESTBackend:
    """Adaptador de compatibilidad para el cliente REST de solo lectura existente."""

    name = "rest"

    def __init__(
        self,
        services: ZohoServices | None = None,
        *,
        config: ZohoSettings | None = None,
    ) -> None:
        self.config = config or (
            services.client.config if services is not None else ZohoSettings.from_django()
        )
        self.services = services or ZohoServices.build(config=self.config)

    def get_organization(self) -> Organization:
        return self.services.metadata.organization()

    def list_modules(self) -> tuple[ModuleMetadata, ...]:
        return self.services.metadata.modules()

    def list_fields(self, module: str) -> tuple[FieldMetadata, ...]:
        return self.services.metadata.fields(module)

    def list_records(
        self,
        module: str,
        *,
        fields: Iterable[str],
        page: int = 1,
        limit: int = 100,
    ) -> Page:
        return self.services.records.list(
            module, fields=fields, page=page, per_page=limit
        )

    def get_record_by_id(
        self, module: str, record_id: str, *, fields: Iterable[str]
    ) -> dict[str, object]:
        clean_module = validate_api_name(module, label="módulo")
        clean_id = (record_id or "").strip()
        if not RECORD_ID.fullmatch(clean_id):
            raise ZohoValidationError("El identificador del registro no es válido.")
        selected = _fields(fields)
        payload = self.services.client.get(
            f"/crm/{API_VERSION}/{clean_module}/{clean_id}",
            params={"fields": ",".join(selected)},
            logical_endpoint="record",
        )
        records = payload.get("data")
        if not isinstance(records, list) or not records or not isinstance(records[0], dict):
            raise ZohoInvalidResponseError("Zoho no devolvió un registro válido.")
        return dict(records[0])

    def search(
        self,
        module: str,
        *,
        criteria: str,
        fields: Iterable[str],
        page: int = 1,
        limit: int = 100,
    ) -> Page:
        clean_module = validate_api_name(module, label="módulo")
        selected = _fields(fields)
        if not criteria or len(criteria) > 2000 or any(x in criteria for x in ("\r", "\n")):
            raise ZohoValidationError("El criterio de búsqueda no es válido.")
        _pagination(page, limit)
        payload = self.services.client.get(
            f"/crm/{API_VERSION}/{clean_module}/search",
            params={
                "criteria": criteria,
                "fields": ",".join(selected),
                "page": page,
                "per_page": limit,
            },
            logical_endpoint="search",
        )
        return page_from_response(payload)

    def execute_coql(self, query: str, *, offset: int = 0, limit: int = 200) -> Page:
        return self.services.coql.query(query, offset=offset, limit=limit)


def _fields(fields: Iterable[str]) -> tuple[str, ...]:
    selected = tuple(validate_api_name(item, label="campo") for item in fields)
    if not selected or len(selected) > MAX_FIELDS_PER_REQUEST:
        raise ZohoValidationError(
            f"Debe seleccionar entre 1 y {MAX_FIELDS_PER_REQUEST} campos."
        )
    return selected


def _pagination(page: int, limit: int) -> None:
    if page < 1 or limit < 1 or limit > MAX_RECORDS_PER_REQUEST:
        raise ZohoValidationError("La paginación solicitada no es válida.")
