from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlsplit

from .client import ZohoClient
from .constants import API_VERSION
from .exceptions import ZohoInvalidResponseError, ZohoValidationError
from .normalization import (
    normalize_choice_text,
    normalize_organization_environment,
    safe_value_class,
)
from .records import validate_api_name
from .schemas import FieldMetadata, ModuleMetadata, Organization

logger = logging.getLogger("integrations.zoho")


class MetadataService:
    def __init__(self, client: ZohoClient):
        self.client = client

    def organization(self) -> Organization:
        payload = self.client.get(
            f"/crm/{API_VERSION}/org", logical_endpoint="organization"
        )
        records = payload.get("org")
        if not isinstance(records, list) or not records or not isinstance(records[0], dict):
            raise ZohoInvalidResponseError(
                "Zoho no devolvió información válida de la organización."
            )
        raw = records[0]
        raw_environment = raw.get("type")
        environment = normalize_organization_environment(raw_environment)
        logger.info(
            "Zoho organization_environment perfil_esperado=%s backend=rest "
            "clase_valor=%s valor_normalizado=%s comparacion=%s",
            self.client.config.profile,
            safe_value_class(raw_environment),
            environment,
            (
                "coincide"
                if environment == self.client.config.environment
                else "no_coincide"
            ),
        )
        return Organization(
            organization_id=str(raw.get("id") or ""),
            company_name=str(raw.get("company_name") or raw.get("alias") or ""),
            primary_email=str(raw.get("primary_email") or ""),
            country=str(raw.get("country") or ""),
            timezone=str(raw.get("time_zone") or raw.get("timezone") or ""),
            currency=str(raw.get("currency") or raw.get("iso_code") or ""),
            data_center=urlsplit(self.client.config.api_base_url).hostname or "",
            environment=environment,
        )

    def modules(self, *, status: str = "") -> tuple[ModuleMetadata, ...]:
        params = {"status": status} if status else None
        payload = self.client.get(
            f"/crm/{API_VERSION}/settings/modules",
            params=params,
            logical_endpoint="modules",
        )
        records = payload.get("modules")
        if not isinstance(records, list):
            raise ZohoInvalidResponseError(
                "Zoho no devolvió metadatos de módulos válidos."
            )
        return tuple(self._module(item) for item in records if isinstance(item, dict))

    def fields(self, module_api_name: str) -> tuple[FieldMetadata, ...]:
        module = validate_api_name(module_api_name, label="módulo")
        payload = self.client.get(
            f"/crm/{API_VERSION}/settings/fields",
            params={"module": module},
            logical_endpoint="fields",
            module=module,
        )
        records = payload.get("fields")
        if not isinstance(records, list):
            raise ZohoInvalidResponseError(
                "Zoho no devolvió metadatos de campos válidos."
            )
        return tuple(self._field(item) for item in records if isinstance(item, dict))

    @staticmethod
    def _module(raw: dict[str, Any]) -> ModuleMetadata:
        api_name = str(raw.get("api_name") or "")
        if not api_name:
            raise ZohoValidationError("Zoho devolvió un módulo sin nombre API.")
        return ModuleMetadata(
            api_name=api_name,
            module_name=str(raw.get("module_name") or ""),
            plural_label=str(raw.get("plural_label") or ""),
            singular_label=str(raw.get("singular_label") or ""),
            custom_module=bool(raw.get("custom_module")),
            generated_type=normalize_choice_text(
                raw.get("generated_type"),
                field="module.generated_type",
                allow_empty=True,
            ),
            api_supported=bool(raw.get("api_supported")),
            status=str(raw.get("status") or ""),
        )

    @staticmethod
    def _field(raw: dict[str, Any]) -> FieldMetadata:
        operation_type = (
            raw.get("operation_type")
            if isinstance(raw.get("operation_type"), dict)
            else {}
        )
        return FieldMetadata(
            api_name=str(raw.get("api_name") or ""),
            field_label=str(raw.get("field_label") or ""),
            data_type=str(raw.get("data_type") or ""),
            required=bool(raw.get("required")),
            read_only=bool(raw.get("read_only"))
            or operation_type.get("api_update") is False,
            custom_field=bool(raw.get("custom_field")),
            length=_optional_int(raw.get("length")),
            pick_list_values=tuple(
                item
                for item in (raw.get("pick_list_values") or [])
                if isinstance(item, dict)
            ),
            lookup=raw.get("lookup") if isinstance(raw.get("lookup"), dict) else {},
            related_details=(
                raw.get("related_details")
                if isinstance(raw.get("related_details"), dict)
                else {}
            ),
            unique=bool(raw.get("unique")),
            system_mandatory=bool(raw.get("system_mandatory")),
        )


def _optional_int(value: object) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None
