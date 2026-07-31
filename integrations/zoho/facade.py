from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .protocols import ZohoBackend
from .records import validate_api_name
from .schemas import FieldMetadata, ModuleMetadata, Organization, Page
from .exceptions import ZohoConfigurationError
from .normalization import normalize_organization_environment
from .settings import ZohoSettings


@dataclass(frozen=True)
class OrganizationFacade:
    backend: ZohoBackend
    config: ZohoSettings

    def get(self) -> Organization:
        organization = self.backend.get_organization()
        reported_environment = normalize_organization_environment(
            organization.environment
        )
        if (
            reported_environment != self.config.environment
        ):
            raise ZohoConfigurationError(
                "El entorno reportado por Zoho no coincide con el perfil solicitado."
            )
        if (
            self.config.expected_org_id
            and organization.organization_id != self.config.expected_org_id
        ):
            raise ZohoConfigurationError(
                "La organizacion reportada por Zoho no coincide con el perfil solicitado."
            )
        return organization


@dataclass(frozen=True)
class MetadataFacade:
    backend: ZohoBackend

    def list_modules(self) -> tuple[ModuleMetadata, ...]:
        return self.backend.list_modules()

    def list_fields(self, module: str) -> tuple[FieldMetadata, ...]:
        return self.backend.list_fields(module)


@dataclass(frozen=True)
class RecordsFacade:
    backend: ZohoBackend

    def list(
        self,
        *,
        module: str,
        fields: Iterable[str],
        page: int = 1,
        limit: int = 100,
    ) -> Page:
        return self.backend.list_records(
            module, fields=tuple(fields), page=page, limit=limit
        )

    def get_by_id(
        self, *, module: str, record_id: str, fields: Iterable[str] = ("id",)
    ) -> dict[str, object]:
        return self.backend.get_record_by_id(
            module, record_id, fields=tuple(fields)
        )


@dataclass(frozen=True)
class SearchFacade:
    backend: ZohoBackend

    def by_field(
        self,
        *,
        module: str,
        field: str,
        value: object,
        fields: Iterable[str] = ("id",),
        page: int = 1,
        limit: int = 100,
    ) -> Page:
        clean_field = validate_api_name(field, label="campo")
        escaped = _criteria_value(str(value))
        return self.backend.search(
            module,
            criteria=f"({clean_field}:equals:{escaped})",
            fields=tuple(fields),
            page=page,
            limit=limit,
        )

    def by_criteria(
        self,
        *,
        module: str,
        criteria: str,
        fields: Iterable[str] = ("id",),
        page: int = 1,
        limit: int = 100,
    ) -> Page:
        return self.backend.search(
            module,
            criteria=criteria,
            fields=tuple(fields),
            page=page,
            limit=limit,
        )


@dataclass(frozen=True)
class CoqlFacade:
    backend: ZohoBackend

    def execute(self, query: str, *, offset: int = 0, limit: int = 200) -> Page:
        return self.backend.execute_coql(query, offset=offset, limit=limit)


class ZohoFacade:
    """Única interfaz pública para consumidores internos."""

    def __init__(
        self,
        backend: ZohoBackend,
        *,
        config: ZohoSettings | None = None,
    ) -> None:
        self.config = (
            config
            or getattr(backend, "config", None)
            or ZohoSettings.from_django()
        )
        self.profile = self.config.profile
        self.environment = self.config.environment
        self.backend_name = backend.name
        self.organization = OrganizationFacade(backend, self.config)
        self.metadata = MetadataFacade(backend)
        self.records = RecordsFacade(backend)
        self.search = SearchFacade(backend)
        self.coql = CoqlFacade(backend)


def _criteria_value(value: str) -> str:
    # Escapado de metacaracteres aceptados por criterios de búsqueda de Zoho.
    return (
        value.replace("\\", "\\\\")
        .replace("(", "\\(")
        .replace(")", "\\)")
        .replace(",", "\\,")
    )
