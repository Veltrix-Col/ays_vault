from __future__ import annotations

import logging
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from typing import Any, Callable

from integrations.zoho import get_zoho
from integrations.zoho.constants import API_VERSION
from integrations.zoho.exceptions import ZohoError

from .constants import ALLOWED_PROFILES, SCHEMA_VERSION
from .normalization import (
    normalize_field,
    normalize_layout,
    normalize_module,
    normalize_related_list,
    picklists_from_field,
    relationship_from_field,
    safe_value,
)


logger = logging.getLogger("integrations.zoho")


class DiscoveryConfigurationError(ValueError):
    pass


class DiscoveryFatalError(RuntimeError):
    """A root discovery failure that makes a snapshot unsafe to publish."""

    def __init__(self, safe_message: str, *, category: str = "fatal"):
        super().__init__(safe_message)
        self.category = category


def normalize_discovery_profile(value: object) -> str:
    profile = str(value or "").strip().casefold()
    if profile not in ALLOWED_PROFILES:
        raise DiscoveryConfigurationError(
            "El perfil es obligatorio y debe ser sandbox o production."
        )
    return profile


def _sdk_version() -> str:
    try:
        return version("ays-zoho-sdk")
    except PackageNotFoundError:
        return "unknown"


def _error(module: str, endpoint: str, exc: Exception) -> dict[str, Any]:
    if isinstance(exc, ZohoError):
        status_code = getattr(exc, "status_code", None)
        zoho_code = str(getattr(exc, "zoho_code", "") or "").upper()
        if status_code == 403:
            category = "permission_denied"
        elif zoho_code in {"INVALID_MODULE", "NOT_SUPPORTED", "FEATURE_NOT_SUPPORTED"}:
            category = "api_not_supported"
        else:
            category = str(exc.category or "unknown")
        return {
            "module": module,
            "endpoint_type": endpoint,
            "category": category,
            "status_code": status_code,
            "safe_message": "Metadata no disponible para esta operación.",
        }
    return {
        "module": module,
        "endpoint_type": endpoint,
        "category": "invalid_response",
        "status_code": None,
        "safe_message": "La respuesta de metadata no pudo interpretarse.",
    }


def _capability(metadata: object, *names: str) -> Callable | None:
    for name in names:
        candidate = getattr(metadata, name, None)
        if callable(candidate):
            return candidate
    return None


def _environment(value: object) -> str:
    getter = getattr(value, "get_value", None)
    if callable(getter):
        try:
            value = getter()
        except Exception:
            return ""
    return str(value or "").strip().casefold()


class DiscoveryService:
    """Collect safe CRM metadata through the public read-only facade only."""

    def __init__(self, *, profile: str, facade=None, clock=None):
        self.profile = normalize_discovery_profile(profile)
        self._facade = facade
        self.clock = clock or (lambda: datetime.now(UTC))

    @property
    def facade(self):
        if self._facade is None:
            self._facade = get_zoho(profile=self.profile)
        return self._facade

    def discover(self) -> dict[str, Any]:
        try:
            facade = self.facade
        except Exception as exc:
            raise DiscoveryFatalError(
                "No fue posible inicializar la configuración o autenticación Zoho.",
                category="authentication",
            ) from exc
        try:
            organization = facade.organization.get()
        except Exception as exc:
            raise DiscoveryFatalError(
                "No fue posible obtener la organización Zoho.",
                category="organization",
            ) from exc
        if organization is None:
            raise DiscoveryFatalError(
                "La respuesta raíz de Organization no es válida.",
                category="invalid_response",
            )
        reported_environment = _environment(
            getattr(organization, "environment", None)
            or getattr(facade, "environment", None)
        )
        if reported_environment != self.profile:
            raise DiscoveryConfigurationError(
                "La organización no coincide con el perfil solicitado."
            )
        if not str(getattr(organization, "organization_id", "") or "").strip():
            raise DiscoveryFatalError(
                "La respuesta raíz de Organization no es válida.",
                category="invalid_response",
            )
        try:
            raw_modules = facade.metadata.list_modules()
            modules = sorted(
                (normalize_module(item) for item in raw_modules),
                key=lambda item: (item.get("api_name", "").casefold(), str(item.get("id", ""))),
            )
        except Exception as exc:
            raise DiscoveryFatalError(
                "No fue posible obtener los módulos Zoho.",
                category="modules",
            ) from exc
        if not modules or any(not str(item.get("api_name") or "").strip() for item in modules):
            raise DiscoveryFatalError(
                "La respuesta raíz de Modules no es válida.",
                category="invalid_response",
            )
        fields: list[dict[str, Any]] = []
        layouts: list[dict[str, Any]] = []
        related_lists: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        fields_ok = 0
        fields_failed = 0

        layout_reader = _capability(facade.metadata, "list_layouts", "layouts")
        related_reader = _capability(
            facade.metadata, "list_related_lists", "related_lists"
        )
        if layout_reader is None:
            errors.append({
                "module": "*", "endpoint_type": "layouts",
                "category": "capability_unavailable", "status_code": None,
                "safe_message": "La fachada instalada no expone metadata de layouts.",
            })
        if related_reader is None:
            errors.append({
                "module": "*", "endpoint_type": "related_lists",
                "category": "capability_unavailable", "status_code": None,
                "safe_message": "La fachada instalada no expone related lists.",
            })

        for module in modules:
            api_name = str(module.get("api_name") or "")
            try:
                discovered = facade.metadata.list_fields(api_name)
                normalized_fields = [normalize_field(module, item) for item in discovered]
                fields.extend(normalized_fields)
                module["fields_status"] = "ok"
                module["fields_metadata_status"] = "available"
                fields_ok += 1
            except Exception as exc:
                failure = _error(api_name, "fields", exc)
                errors.append(failure)
                module["fields_status"] = "error"
                module["fields_error_category"] = failure["category"]
                module["fields_metadata_status"] = "unavailable"
                module["fields_metadata_error"] = failure["category"]
                fields_failed += 1

            if layout_reader is not None:
                try:
                    normalized_layouts = [
                        normalize_layout(module, item) for item in layout_reader(api_name)
                    ]
                    layouts.extend(normalized_layouts)
                    module["layouts_status"] = "ok"
                except Exception as exc:
                    failure = _error(api_name, "layouts", exc)
                    errors.append(failure)
                    module["layouts_status"] = "error"
                    module["layouts_error_category"] = failure["category"]
            else:
                module["layouts_status"] = "unavailable"
            if related_reader is not None:
                try:
                    normalized_related = [
                        normalize_related_list(module, item)
                        for item in related_reader(api_name)
                    ]
                    related_lists.extend(normalized_related)
                    module["related_lists_status"] = "ok"
                except Exception as exc:
                    failure = _error(api_name, "related_lists", exc)
                    errors.append(failure)
                    module["related_lists_status"] = "error"
                    module["related_lists_error_category"] = failure["category"]
            else:
                module["related_lists_status"] = "unavailable"

        fields.sort(key=lambda item: (
            item.get("module_api_name", "").casefold(),
            item.get("api_name", "").casefold(),
            str(item.get("field_id", "")),
        ))
        relationships = [
            relation for field in fields
            if (relation := relationship_from_field(field)) is not None
        ]
        relationships.sort(key=lambda item: (
            item["source_module"].casefold(), item["source_field_api_name"].casefold()
        ))
        picklists = [item for field in fields for item in picklists_from_field(field)]
        picklists.sort(key=lambda item: (
            item["module_api_name"].casefold(), item["field_api_name"].casefold(),
            item.get("sequence_number") if isinstance(item.get("sequence_number"), int) else 10**9,
            str(item.get("actual_value", "")).casefold(),
        ))
        subforms = self._subforms(fields)
        for relation in relationships:
            if not relation.get("resolved"):
                errors.append({
                    "module": relation["source_module"],
                    "endpoint_type": "lookup",
                    "category": "metadata_target_missing",
                    "status_code": None,
                    "safe_message": "El destino del lookup no pudo resolverse con metadata.",
                })
        for subform in subforms:
            if not subform.get("resolved"):
                errors.append({
                    "module": subform["parent_module"],
                    "endpoint_type": "subforms",
                    "category": "metadata_target_missing",
                    "status_code": None,
                    "safe_message": "El destino del subform no pudo resolverse con metadata.",
                })
        layouts.sort(key=lambda item: (
            item["module_api_name"].casefold(), item["layout_name"].casefold(), item["layout_id"]
        ))
        related_lists.sort(key=lambda item: (
            item["source_module"].casefold(), item["api_name"].casefold(),
            item["related_list_name"].casefold(),
        ))
        errors.sort(key=lambda item: (
            item["module"].casefold(), item["endpoint_type"], item["category"]
        ))
        status = "partial" if errors else "success"

        generated_at = self.clock().astimezone(UTC).isoformat()
        org_data = {
            "organization_id": str(getattr(organization, "organization_id", "") or ""),
            "company_name": str(getattr(organization, "company_name", "") or ""),
            "environment": reported_environment,
            "country": str(getattr(organization, "country", "") or ""),
            "currency": str(getattr(organization, "currency", "") or ""),
            "timezone": str(getattr(organization, "timezone", "") or ""),
        }
        result = {
            "manifest": {
                "schema_version": SCHEMA_VERSION,
                "profile": self.profile,
                "environment": reported_environment,
                "generated_at": generated_at,
                "sdk_version": _sdk_version(),
                "api_version": API_VERSION,
                "backend": str(getattr(facade, "backend_name", "unknown")),
                "organization_id": org_data["organization_id"],
                "organization_name": org_data["company_name"],
                "status": status,
                "modules_total": len(modules),
                "modules_fields_ok": fields_ok,
                "modules_fields_failed": fields_failed,
                "relationships_total": len(relationships),
                "subforms_total": len(subforms),
                "layouts_total": len(layouts),
                "related_lists_total": len(related_lists),
                "errors_total": len(errors),
                "read_only": True,
                "source": "zoho_metadata",
            },
            "organization": org_data,
            "modules": modules,
            "fields": fields,
            "layouts": layouts,
            "relationships": relationships,
            "related_lists": related_lists,
            "subforms": subforms,
            "picklists": picklists,
            "errors": errors,
        }
        logger.info(
            "zoho_discovery profile=%s backend=%s modules=%d fields_ok=%d "
            "fields_failed=%d relationships=%d errors=%d mode=read_only",
            self.profile, result["manifest"]["backend"], len(modules), fields_ok,
            fields_failed, len(relationships), len(errors),
        )
        return result

    @staticmethod
    def _subforms(fields: list[dict[str, Any]]) -> list[dict[str, Any]]:
        by_module: dict[str, list[str]] = {}
        for field in fields:
            by_module.setdefault(field["module_api_name"], []).append(field["api_name"])
        result = []
        for field in fields:
            if str(field.get("data_type") or "").casefold().replace("_", "") != "subform":
                continue
            details = field.get("subform") or field.get("associated_module") or field.get("lookup") or {}
            details = details if isinstance(details, dict) else {}
            module_data = details.get("module") if isinstance(details.get("module"), dict) else details
            api_name = str(
                module_data.get("api_name")
                or module_data.get("module_name")
                or details.get("api_name")
                or ""
            )
            subform_id = str(module_data.get("id") or details.get("id") or "")
            resolved = bool(api_name or subform_id)
            result.append({
                "parent_module": field["module_api_name"],
                "parent_module_id": field.get("module_id", ""),
                "parent_field": field.get("field_label", ""),
                "parent_field_api_name": field["api_name"],
                "parent_field_id": field.get("field_id", ""),
                "subform_module": api_name,
                "subform_module_api_name": api_name,
                "subform_id": subform_id,
                "fields": sorted(by_module.get(api_name, []), key=str.casefold),
                "resolved": resolved,
                "reason": "" if resolved else "metadata_target_missing",
            })
        return sorted(result, key=lambda item: (
            item["parent_module"].casefold(), item["parent_field_api_name"].casefold()
        ))
