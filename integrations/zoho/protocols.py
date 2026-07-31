from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from .schemas import FieldMetadata, ModuleMetadata, Organization, Page


class ZohoBackend(Protocol):
    name: str

    def get_organization(self) -> Organization: ...
    def list_modules(self) -> tuple[ModuleMetadata, ...]: ...
    def list_fields(self, module: str) -> tuple[FieldMetadata, ...]: ...
    def list_records(
        self, module: str, *, fields: Iterable[str], page: int, limit: int
    ) -> Page: ...
    def get_record_by_id(
        self, module: str, record_id: str, *, fields: Iterable[str]
    ) -> dict[str, object]: ...
    def search(
        self,
        module: str,
        *,
        criteria: str,
        fields: Iterable[str],
        page: int,
        limit: int,
    ) -> Page: ...
    def execute_coql(self, query: str, *, offset: int, limit: int) -> Page: ...
