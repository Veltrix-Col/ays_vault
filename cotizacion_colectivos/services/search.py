from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable

from cotizacion_colectivos.dto import CompanySearchResult, PersonSearchResult
from integrations.zoho.exceptions import ZohoError

from .common import (
    ColectivosServiceError,
    escape_criteria_value,
    mask_document,
    colectivos_zoho,
    get_colectivos_profile,
    sign_record_id,
    translate_zoho_error,
)
from .mappings import (
    COMPANY_ID_TYPE,
    COMPANY_TYPE,
    CONTACT_SEARCH_FIELDS,
    CONTACTS_MODULE,
    PERSON_ID_TYPE,
    PERSON_TYPE,
    SEARCH_LIMIT,
)


logger = logging.getLogger("cotizacion_colectivos")


def _fixed_criteria(
    person_type: str,
    id_type: str,
    field_criteria: str,
    *,
    include_id_type: bool = True,
) -> str:
    criteria = [f"(Tipo_de_persona:equals:{person_type})"]
    if include_id_type:
        criteria.append(f"(Tipo_ID:equals:{id_type})")
    criteria.append(field_criteria)
    return f"({'and'.join(criteria)})"


class _BaseSearchService:
    person_type = ""
    id_type = ""
    include_remote_id_type = True
    entity_kind = "unknown"

    def __init__(self, zoho=None):
        self.profile = get_colectivos_profile()
        try:
            self.zoho = zoho or colectivos_zoho()
        except ZohoError as exc:
            raise translate_zoho_error(exc, self.profile) from exc

    def _query(self, field_criteria: str) -> tuple[dict[str, object], ...]:
        started = time.monotonic()
        try:
            page = self.zoho.search.by_criteria(
                module=CONTACTS_MODULE,
                criteria=_fixed_criteria(
                    self.person_type,
                    self.id_type,
                    field_criteria,
                    include_id_type=self.include_remote_id_type,
                ),
                fields=CONTACT_SEARCH_FIELDS,
                page=1,
                limit=SEARCH_LIMIT,
            )
        except ZohoError as exc:
            logger.warning(
                "colectivos_search_api application=cotizacion_colectivos "
                "entity=%s operation=search_api duration_ms=%d results=0 "
                "error=%s profile=%s",
                self.entity_kind,
                round((time.monotonic() - started) * 1000),
                exc.category,
                self.profile,
            )
            raise
        logger.info(
            "colectivos_search_api application=cotizacion_colectivos "
            "entity=%s operation=search_api duration_ms=%d results=%d "
            "error=none profile=%s",
            self.entity_kind,
            round((time.monotonic() - started) * 1000),
            min(len(page.records), SEARCH_LIMIT),
            self.profile,
        )
        return tuple(page.records[:SEARCH_LIMIT])

    def _accept_record(self, record: dict[str, object]) -> bool:
        return True

    def _collect(
        self,
        queries: Iterable[Callable[[], tuple[dict[str, object], ...]]],
        mapper: Callable[[dict[str, object]], object],
        ranker: Callable[[dict[str, object]], tuple[int, str]] | None = None,
        stop_after_first_nonempty: bool = False,
    ) -> tuple[object, ...]:
        records: dict[str, dict[str, object]] = {}
        try:
            for query in queries:
                for record in query():
                    if not self._accept_record(record):
                        continue
                    record_id = str(record.get("id") or "")
                    if record_id and record_id not in records:
                        records[record_id] = record
                    if len(records) >= SEARCH_LIMIT:
                        break
                if len(records) >= SEARCH_LIMIT:
                    break
                if stop_after_first_nonempty and records:
                    break
        except ColectivosServiceError:
            raise
        except ZohoError as exc:
            raise translate_zoho_error(exc, self.profile) from exc
        ordered = list(records.values())
        if ranker:
            ordered.sort(key=ranker)
        return tuple(mapper(record) for record in ordered[:SEARCH_LIMIT])


class CompanySearchService(_BaseSearchService):
    person_type = COMPANY_TYPE
    id_type = COMPANY_ID_TYPE
    include_remote_id_type = False
    entity_kind = "company"

    def search(self, query: str) -> tuple[CompanySearchResult, ...]:
        value = escape_criteria_value(query.strip())
        if query.isdigit():
            queries = (
                lambda: self._query(f"(N_mero_de_ID:equals:{value})"),
                lambda: self._query(f"(N_mero_de_ID:starts_with:{value})"),
            )
        else:
            clean = _validated_name(query)
            queries = (
                lambda: self._query(f"(Nombre_comercial:equals:{value})"),
                lambda: self._query(f"(Nombre_comercial:starts_with:{value})"),
                lambda: self._query(f"(Raz_n_social:equals:{value})"),
                lambda: self._query(f"(Raz_n_social:starts_with:{value})"),
                lambda: self._query(f"(Full_Name:equals:{value})"),
                lambda: self._query(f"(Full_Name:starts_with:{value})"),
            )
            folded = clean.casefold()
            return self._collect(queries, self._map, lambda record: _name_rank(
                record, folded, ("Nombre_comercial", "Raz_n_social", "Full_Name")
            ))
        return self._collect(
            queries,
            self._map,
            stop_after_first_nonempty=True,
        )

    def _accept_record(self, record: dict[str, object]) -> bool:
        if record.get("Tipo_de_persona") != COMPANY_TYPE:
            return False
        # Production confirmó que Tipo_ID=NIT no es fiable como filtro remoto.
        # Localmente se rechaza cualquier valor poblado distinto de NIT. Un
        # valor vacío se conserva para no ocultar empresas jurídicas válidas
        # con clasificación documental incompleta.
        id_type = str(record.get("Tipo_ID") or "").strip()
        return not id_type or id_type == COMPANY_ID_TYPE

    def _map(self, record) -> CompanySearchResult:
        if not self._accept_record(record):
            raise ColectivosServiceError(
                "invalid_response",
                f"{'Producción' if self.profile == 'production' else 'Sandbox'} devolvió una respuesta inválida.",
            )
        name = str(record.get("Nombre_comercial") or record.get("Raz_n_social") or "Empresa sin nombre")
        return CompanySearchResult(
            detail_token=sign_record_id(record.get("id"), "company"),
            display_name=name,
            masked_document=mask_document(record.get("N_mero_de_ID")),
            state=str(record.get("Estado") or "Sin estado"),
        )


class PersonSearchService(_BaseSearchService):
    person_type = PERSON_TYPE
    id_type = PERSON_ID_TYPE
    entity_kind = "person"

    def search(self, query: str) -> tuple[PersonSearchResult, ...]:
        value = escape_criteria_value(query.strip())
        if query.isdigit():
            queries = (
                lambda: self._query(f"(N_mero_de_ID:equals:{value})"),
                lambda: self._query(f"(N_mero_de_ID:starts_with:{value})"),
            )
        else:
            clean = _validated_name(query)
            queries = (
                lambda: self._query(f"(Full_Name:equals:{value})"),
                lambda: self._query(f"(Full_Name:starts_with:{value})"),
                lambda: self._query(f"(First_Name:equals:{value})"),
                lambda: self._query(f"(First_Name:starts_with:{value})"),
                lambda: self._query(f"(Last_Name:equals:{value})"),
                lambda: self._query(f"(Last_Name:starts_with:{value})"),
            )
            folded = clean.casefold()
            return self._collect(queries, self._map, lambda record: _name_rank(
                record, folded, ("Full_Name", "First_Name", "Last_Name")
            ))
        return self._collect(queries, self._map)

    def _map(self, record) -> PersonSearchResult:
        if record.get("Tipo_de_persona") != PERSON_TYPE or record.get("Tipo_ID") != PERSON_ID_TYPE:
            raise ColectivosServiceError(
                "invalid_response",
                f"{'Producción' if self.profile == 'production' else 'Sandbox'} devolvió una respuesta inválida.",
            )
        return PersonSearchResult(
            detail_token=sign_record_id(record.get("id"), "person"),
            full_name=str(record.get("Full_Name") or "Persona sin nombre"),
            masked_document=mask_document(record.get("N_mero_de_ID")),
            state=str(record.get("Estado") or "Sin estado"),
        )


def _validated_name(value: str) -> str:
    clean = value.strip()
    if len(clean) < 3 or any(
        not (character.isalnum() or character.isspace() or character in ".&'’-")
        for character in clean
    ):
        raise ColectivosServiceError("invalid_query", "El criterio de búsqueda no es válido.")
    return clean


def _name_rank(record: dict[str, object], query: str, fields: tuple[str, ...]) -> tuple[int, str]:
    for index, field in enumerate(fields):
        value = str(record.get(field) or "").strip().casefold()
        if value == query:
            return index * 2, value
        if query in value:
            return index * 2 + 1, value
    return len(fields) * 2, ""
