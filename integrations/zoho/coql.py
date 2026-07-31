from __future__ import annotations

import re

from .client import ZohoClient
from .constants import API_VERSION, MAX_COQL_LENGTH, MAX_COQL_RECORDS
from .exceptions import ZohoValidationError
from .pagination import page_from_response
from .schemas import Page

READ_QUERY = re.compile(r"^\s*select\b", re.IGNORECASE)
FORBIDDEN = re.compile(
    r"\b(insert|update|delete|upsert|create|drop|alter|truncate|merge|execute|call)\b",
    re.IGNORECASE,
)
LIMIT = re.compile(r"\blimit\s+(\d+)\s*(?:,\s*(\d+))?\s*$", re.IGNORECASE)


class CoqlService:
    def __init__(self, client: ZohoClient):
        self.client = client

    def query(
        self,
        select_query: str,
        *,
        offset: int = 0,
        limit: int = 200,
    ) -> Page:
        query = (select_query or "").strip()
        if not query or not READ_QUERY.match(query):
            raise ZohoValidationError("COQL solo admite consultas SELECT.")
        if len(query) > MAX_COQL_LENGTH:
            raise ZohoValidationError("La consulta COQL supera el tamaño permitido.")
        if ";" in query or "--" in query or "/*" in query or FORBIDDEN.search(query):
            raise ZohoValidationError("La consulta COQL contiene una instrucción no permitida.")
        if offset < 0 or limit < 1 or limit > MAX_COQL_RECORDS:
            raise ZohoValidationError("La paginación COQL no es válida.")
        found = LIMIT.search(query)
        if found:
            requested = int(found.group(2) or found.group(1))
            if requested > MAX_COQL_RECORDS:
                raise ZohoValidationError(
                    f"COQL no puede solicitar más de {MAX_COQL_RECORDS} registros."
                )
        else:
            query = f"{query} limit {offset}, {limit}"
        payload = self.client.post_read(
            f"/crm/{API_VERSION}/coql",
            json={"select_query": query},
            logical_endpoint="coql",
        )
        return page_from_response(payload)
