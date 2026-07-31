from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable

from ..constants import MAX_FIELDS_PER_REQUEST, MAX_RECORDS_PER_REQUEST
from ..exceptions import (
    ZohoAuthenticationError,
    ZohoAuthorizationError,
    ZohoConfigurationError,
    ZohoInvalidResponseError,
    ZohoNotFoundError,
    ZohoRateLimitError,
    ZohoSDKError,
    ZohoTimeoutError,
    ZohoValidationError,
)
from ..records import validate_api_name
from ..normalization import (
    normalize_choice_text,
    normalize_organization_environment,
    safe_value_class,
)
from ..schemas import FieldMetadata, ModuleMetadata, Organization, Page
from ..sdk.initializer import initialize_sdk, sdk_execution_context
from ..sdk.response_parser import (
    getter,
    normalize_sdk_exception,
    record_dict,
    response_object,
)
from ..settings import ZohoSettings
from .rest import RECORD_ID, RESTBackend

logger = logging.getLogger("integrations.zoho")
FALLBACK_ERRORS = (ZohoSDKError, ZohoInvalidResponseError, ZohoNotFoundError)


class SDKBackend:
    """Backend oficial V8. Solo importa e inicializa el SDK al primer uso."""

    name = "sdk"

    def __init__(
        self,
        *,
        config: ZohoSettings | None = None,
        rest_fallback: RESTBackend | None = None,
        initializer: Callable[..., object] = initialize_sdk,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config or ZohoSettings.from_django()
        # El fallback debe pertenecer siempre al mismo perfil. Construirlo con
        # la configuración activa global permitiría un cruce accidental entre
        # Sandbox y Producción al instanciar este backend directamente.
        self.rest = rest_fallback or RESTBackend(config=self.config)
        self._initializer = initializer
        self._sleeper = sleeper

    def _ready(self) -> None:
        self._initializer(self.config)

    def get_organization(self) -> Organization:
        def operation() -> Organization:
            from zohocrmsdk.src.com.zoho.crm.api.org.org_operations import OrgOperations

            raw = response_object(
                OrgOperations().get_organization(), operation="organization"
            )
            items = getter(raw, "org", [])
            if not items:
                raise ZohoInvalidResponseError(
                    "Zoho no devolvió información de organización."
                )
            item = items[0]
            raw_environment = getter(item, "type", None)
            environment = normalize_organization_environment(raw_environment)
            matches_profile = environment == self.config.environment
            logger.info(
                "Zoho organization_environment perfil_esperado=%s backend=sdk "
                "clase_valor=%s valor_normalizado=%s comparacion=%s",
                self.config.profile,
                safe_value_class(raw_environment),
                environment,
                "coincide" if matches_profile else "no_coincide",
            )
            return Organization(
                organization_id=str(getter(item, "id", "")),
                company_name=str(
                    getter(item, "company_name", "") or getter(item, "alias", "")
                ),
                primary_email=str(getter(item, "primary_email", "")),
                country=str(getter(item, "country", "")),
                timezone=str(
                    getter(item, "time_zone", "") or getter(item, "timezone", "")
                ),
                currency=str(
                    getter(item, "currency", "") or getter(item, "iso_code", "")
                ),
                data_center=self.config.data_center,
                environment=environment,
            )

        return self._call("organization", operation)

    def list_modules(self) -> tuple[ModuleMetadata, ...]:
        def operation() -> tuple[ModuleMetadata, ...]:
            from zohocrmsdk.src.com.zoho.crm.api.modules.modules_operations import (
                ModulesOperations,
            )

            raw = response_object(ModulesOperations().get_modules(), operation="modules")
            items = getter(raw, "modules", [])
            return tuple(self._module(item) for item in items)

        return self._call_with_fallback(
            "modules", operation, self.rest.list_modules
        )

    def list_fields(self, module: str) -> tuple[FieldMetadata, ...]:
        clean = validate_api_name(module, label="módulo")
        try:
            return self._list_fields_sdk(clean)
        except FALLBACK_ERRORS as exc:
            # Fields usa REST como compatibilidad prevista; el consumidor decide
            # si el resultado final amerita warning según la clasificación.
            logger.info(
                "Zoho backend=sdk operacion=fields modulo=%s fallback=rest categoria=%s",
                clean,
                exc.category,
            )
            return self.rest.list_fields(clean)

    def list_fields_sdk_only(self, module: str) -> tuple[FieldMetadata, ...]:
        """Consulta Fields mediante SDK sin ocultar el resultado con el fallback."""
        clean = validate_api_name(module, label="módulo")
        return self._list_fields_sdk(clean)

    def _list_fields_sdk(self, clean: str) -> tuple[FieldMetadata, ...]:
        def operation() -> tuple[FieldMetadata, ...]:
            from zohocrmsdk.src.com.zoho.crm.api.fields.fields_operations import (
                FieldsOperations,
                GetFieldsParam,
            )
            from zohocrmsdk.src.com.zoho.crm.api.parameter_map import ParameterMap

            params = ParameterMap()
            params.add(GetFieldsParam.module, clean)
            raw = response_object(
                FieldsOperations().get_fields(params),
                operation="fields",
                module=clean,
            )
            if raw is None:
                return ()
            return tuple(self._field(item) for item in getter(raw, "fields", []))

        return self._call("fields", operation, module=clean)

    def list_records(
        self,
        module: str,
        *,
        fields: Iterable[str],
        page: int = 1,
        limit: int = 100,
    ) -> Page:
        clean = validate_api_name(module, label="módulo")
        selected = _fields(fields)
        _pagination(page, limit)

        def operation() -> Page:
            from zohocrmsdk.src.com.zoho.crm.api.parameter_map import ParameterMap
            from zohocrmsdk.src.com.zoho.crm.api.record.record_operations import (
                GetRecordsParam,
                RecordOperations,
            )

            params = ParameterMap()
            params.add(GetRecordsParam.fields, ",".join(selected))
            params.add(GetRecordsParam.page, page)
            params.add(GetRecordsParam.per_page, limit)
            raw = response_object(
                RecordOperations(clean).get_records(params), operation="records"
            )
            if raw is None:
                return Page(records=(), page=page, count=0)
            records = tuple(record_dict(item) for item in getter(raw, "data", []))
            return _page(raw, records, page)

        return self._call("records", operation, module=clean)

    def get_record_by_id(
        self, module: str, record_id: str, *, fields: Iterable[str]
    ) -> dict[str, object]:
        clean = validate_api_name(module, label="módulo")
        clean_id = (record_id or "").strip()
        if not RECORD_ID.fullmatch(clean_id):
            raise ZohoValidationError("El identificador del registro no es válido.")
        selected = _fields(fields)

        def operation() -> dict[str, object]:
            from zohocrmsdk.src.com.zoho.crm.api.parameter_map import ParameterMap
            from zohocrmsdk.src.com.zoho.crm.api.record.record_operations import (
                GetRecordParam,
                RecordOperations,
            )

            params = ParameterMap()
            params.add(GetRecordParam.fields, ",".join(selected))
            raw = response_object(
                RecordOperations(clean).get_record(clean_id, params),
                operation="record",
            )
            records = getter(raw, "data", [])
            if not records:
                raise ZohoNotFoundError("El registro solicitado no existe en Zoho.")
            return record_dict(records[0])

        return self._call("record", operation, module=clean)

    def search(
        self,
        module: str,
        *,
        criteria: str,
        fields: Iterable[str],
        page: int = 1,
        limit: int = 100,
    ) -> Page:
        clean = validate_api_name(module, label="módulo")
        selected = _fields(fields)
        _pagination(page, limit)
        if not criteria or len(criteria) > 2000 or any(x in criteria for x in ("\r", "\n")):
            raise ZohoValidationError("El criterio de búsqueda no es válido.")

        def operation() -> Page:
            from zohocrmsdk.src.com.zoho.crm.api.parameter_map import ParameterMap
            from zohocrmsdk.src.com.zoho.crm.api.record.record_operations import (
                RecordOperations,
                SearchRecordsParam,
            )

            params = ParameterMap()
            params.add(SearchRecordsParam.criteria, criteria)
            params.add(SearchRecordsParam.fields, ",".join(selected))
            params.add(SearchRecordsParam.page, page)
            params.add(SearchRecordsParam.per_page, limit)
            raw = response_object(
                RecordOperations(clean).search_records(params), operation="search"
            )
            if raw is None:
                return Page(records=(), page=page, count=0)
            records = tuple(record_dict(item) for item in getter(raw, "data", []))
            return _page(raw, records, page)

        return self._call("search", operation, module=clean)

    def execute_coql(self, query: str, *, offset: int = 0, limit: int = 200) -> Page:
        # El cliente REST existente conserva la validación SELECT y los límites.
        logger.info("Zoho backend=sdk operacion=coql fallback=rest")
        return self.rest.execute_coql(query, offset=offset, limit=limit)

    def _call(self, logical: str, operation: Callable, *, module: str = ""):
        with sdk_execution_context(self.config, self._initializer):
            return self._call_ready(logical, operation, module=module)

    def _call_ready(self, logical: str, operation: Callable, *, module: str = ""):
        started = time.monotonic()
        attempts = self.config.max_retries + 1
        for attempt in range(attempts):
            try:
                value = operation()
                break
            except (
                ZohoConfigurationError,
                ZohoAuthenticationError,
                ZohoAuthorizationError,
                ZohoValidationError,
            ):
                raise
            except (ZohoRateLimitError, ZohoTimeoutError) as exc:
                if attempt + 1 >= attempts:
                    raise
                logger.warning(
                    "Zoho backend=sdk operacion=%s reintento=%s categoria=%s",
                    logical,
                    attempt + 2,
                    exc.category,
                )
                self._sleeper(min(4.0, 0.5 * (2**attempt)))
            except ZohoSDKError as exc:
                if not exc.status_code or exc.status_code < 500 or attempt + 1 >= attempts:
                    raise
                self._sleeper(min(4.0, 0.5 * (2**attempt)))
            except ZohoInvalidResponseError:
                raise
            except ZohoNotFoundError:
                raise
            except Exception as exc:
                logger.warning(
                    "Zoho backend=sdk operacion=%s modulo=%s categoria=sdk clase=%s",
                    logical,
                    module or "n/a",
                    exc.__class__.__name__[:80],
                )
                raise normalize_sdk_exception(
                    exc,
                    operation=logical,
                    module=module,
                    request_sent=None,
                ) from None
        logger.info(
            "Zoho backend=sdk operacion=%s modulo=%s duracion_ms=%s",
            logical,
            module or "n/a",
            int((time.monotonic() - started) * 1000),
        )
        return value

    def _call_with_fallback(
        self,
        logical: str,
        operation: Callable,
        fallback: Callable,
        *,
        module: str = "",
    ):
        try:
            return self._call(logical, operation, module=module)
        except FALLBACK_ERRORS as exc:
            logger.warning(
                "Zoho backend=sdk operacion=%s modulo=%s fallback=rest categoria=%s",
                logical,
                module or "n/a",
                exc.category,
            )
            return fallback()

    @staticmethod
    def _module(item: object) -> ModuleMetadata:
        return ModuleMetadata(
            api_name=str(getter(item, "api_name", "")),
            module_name=str(getter(item, "module_name", "")),
            plural_label=str(getter(item, "plural_label", "")),
            singular_label=str(getter(item, "singular_label", "")),
            custom_module=bool(getter(item, "custom_module", False)),
            generated_type=normalize_choice_text(
                getter(item, "generated_type", ""),
                field="module.generated_type",
                allow_empty=True,
            ),
            api_supported=bool(getter(item, "api_supported", False)),
            status=str(getter(item, "status", "")),
        )

    @staticmethod
    def _field(item: object) -> FieldMetadata:
        operation_type = getter(item, "operation_type")
        api_update = getter(operation_type, "api_update", None) if operation_type else None
        picklists = tuple(
            {
                "display_value": str(getter(value, "display_value", "")),
                "actual_value": str(getter(value, "actual_value", "")),
            }
            for value in getter(item, "pick_list_values", [])
        )
        return FieldMetadata(
            api_name=str(getter(item, "api_name", "")),
            field_label=str(getter(item, "field_label", "")),
            data_type=str(getter(item, "data_type", "")),
            required=bool(getter(item, "required", False)),
            read_only=bool(getter(item, "read_only", False)) or api_update is False,
            custom_field=bool(getter(item, "custom_field", False)),
            length=_optional_int(getter(item, "length")),
            pick_list_values=picklists,
            unique=bool(getter(item, "unique", False)),
            system_mandatory=bool(getter(item, "system_mandatory", False)),
        )


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


def _page(raw: object, records: tuple[dict[str, object], ...], page: int) -> Page:
    info = getter(raw, "info")
    return Page(
        records=records,
        more_records=bool(getter(info, "more_records", False)) if info else False,
        page=int(getter(info, "page", page)) if info else page,
        count=int(getter(info, "count", len(records))) if info else len(records),
    )


def _optional_int(value: object) -> int | None:
    try:
        return None if value in (None, "") else int(str(value))
    except (TypeError, ValueError):
        return None
