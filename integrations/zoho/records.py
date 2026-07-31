from __future__ import annotations

import re
from collections.abc import Iterable

from .client import ZohoClient
from .constants import (
    API_VERSION,
    MAX_FIELDS_PER_REQUEST,
    MAX_RECORDS_PER_REQUEST,
)
from .exceptions import ZohoValidationError
from .pagination import page_from_response
from .schemas import Page

API_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


def validate_api_name(value: str, *, label: str) -> str:
    clean = (value or "").strip()
    if not API_NAME.fullmatch(clean):
        raise ZohoValidationError(f"El nombre API del {label} no es válido.")
    return clean


class RecordsService:
    def __init__(self, client: ZohoClient):
        self.client = client

    def list(
        self,
        module_api_name: str,
        *,
        fields: Iterable[str],
        page: int = 1,
        per_page: int = 100,
        page_token: str = "",
    ) -> Page:
        module = validate_api_name(module_api_name, label="módulo")
        selected = tuple(validate_api_name(item, label="campo") for item in fields)
        if not selected or len(selected) > MAX_FIELDS_PER_REQUEST:
            raise ZohoValidationError(
                f"Debe seleccionar entre 1 y {MAX_FIELDS_PER_REQUEST} campos."
            )
        if page < 1:
            raise ZohoValidationError("La página solicitada no es válida.")
        if per_page < 1 or per_page > MAX_RECORDS_PER_REQUEST:
            raise ZohoValidationError(
                f"El límite por llamada debe estar entre 1 y {MAX_RECORDS_PER_REQUEST}."
            )
        params: dict[str, object] = {
            "fields": ",".join(selected),
            "page": page,
            "per_page": per_page,
        }
        if page_token:
            if len(page_token) > 512:
                raise ZohoValidationError("El token de paginación no es válido.")
            params["page_token"] = page_token
        payload = self.client.get(
            f"/crm/{API_VERSION}/{module}",
            params=params,
            logical_endpoint="records",
        )
        return page_from_response(payload)
