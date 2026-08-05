from __future__ import annotations

import logging
import re
import time

from django.conf import settings

from cotizacion_colectivos.branches import classify_branch
from cotizacion_colectivos.dto import ContactSummary, GroupMember, PolicyDetail, RelatedInsured, RelatedRisk
from integrations.zoho.exceptions import ZohoError

from .common import (
    ColectivosServiceError,
    ZOHO_ID,
    colectivos_zoho,
    escape_criteria_value,
    functional_reference,
    get_colectivos_profile,
    mask_document,
    mask_reference,
    sign_record_id,
    translate_zoho_error,
    unsign_record_context,
    unsign_record_id,
)
from .preparations import (
    invalidate_policy_preparation,
    load_policy_preparation,
    store_policy_preparation,
)
from .entity_detail import _layout, _lookup_id, _text
from .mappings import (
    INSURED_MODULE,
    INSURED_CONTACT_ROLES,
    INSURED_RELATION_FIELDS,
    POLICIES_MODULE,
    POLICY_DETAIL_FIELDS,
    RISKS_MODULE,
    RISK_DETAIL_FIELDS,
)

POLICY_GROUP_PAGE_SIZE = settings.COLECTIVOS_GROUP_PAGE_SIZE
POLICY_GROUP_LIMIT = settings.COLECTIVOS_GROUP_MAX_RECORDS
RELATED_BATCH_SIZE = 100
CONTACT_BATCH_FIELDS = (
    "id", "Tipo_de_persona", "Tipo_ID", "N_mero_de_ID", "Full_Name",
    "First_Name", "Last_Name", "Nombre_comercial", "Raz_n_social", "Estado",
    "Email", "Phone", "Mobile", "Ciudad_de_direcci_n_principal", "Direcci_n",
)
logger = logging.getLogger("cotizacion_colectivos")
SAFE_ERROR_CODE = re.compile(r"[A-Z][A-Z0-9_]{0,79}")


def _fixed_batch_query(module: str, fields: tuple[str, ...], ids: set[str]) -> str:
    if module not in {"Contacts", RISKS_MODULE} or not ids or any(not ZOHO_ID.fullmatch(value) for value in ids):
        raise ColectivosServiceError("invalid_response", "No fue posible consultar la información relacionada.")
    safe_ids = ",".join(f"'{value}'" for value in sorted(ids))
    return f"select {','.join(fields)} from {module} where id in ({safe_ids}) limit {RELATED_BATCH_SIZE}"


class PolicyService:
    def __init__(self, zoho=None, *, use_preparation: bool | None = None):
        profile_started = time.monotonic()
        self.profile = get_colectivos_profile()
        self.timings = {
            "profile_resolution_ms": round((time.monotonic() - profile_started) * 1000),
            "facade_ms": 0, "organization_ms": 0,
            "policy_lookup_ms": 0, "policy_search_ms": 0,
            "risks1_query_ms": 0, "contacts_query_ms": 0,
            "risks_query_ms": 0, "parsing_ms": 0, "dto_ms": 0,
            "grouping_ms": 0, "snapshot_validation_ms": 0,
            "snapshot_serialization_ms": 0, "encryption_ms": 0,
            "database_insert_ms": 0, "registro_bulk_create_ms": 0,
            "request_creation_ms": 0, "access_creation_ms": 0,
            "workspace_persistence_ms": 0, "total_ms": 0,
            "remote_queries": 0, "organization_queries": 0,
            "records_queries": 0, "search_queries": 0, "coql_queries": 0,
            "metadata_queries": 0, "insured_pages": 0,
            "contacts_batches": 0, "risks_batches": 0,
        }
        self.preparation_status = "disabled"
        self.preparation_metadata = {}
        self.last_detail = None
        self.last_members = ()
        self.use_preparation = (zoho is None) if use_preparation is None else use_preparation
        self.zoho = zoho
        self.backend = str(
            getattr(zoho, "backend_name", getattr(settings, "ZOHO_BACKEND", "sdk"))
        ).strip().lower()

    def _ensure_zoho(self):
        if self.zoho is not None:
            return self.zoho
        try:
            facade_started = time.monotonic()
            self.zoho = colectivos_zoho(timings=self.timings)
            self.timings["facade_ms"] = round((time.monotonic() - facade_started) * 1000)
            if not self.timings.get("organization_cache_hit", 0):
                self.timings["organization_queries"] += 1
                self.timings["remote_queries"] += 1
        except ZohoError as exc:
            raise translate_zoho_error(exc, self.profile) from exc
        self.backend = str(getattr(self.zoho, "backend_name", self.backend)).strip().lower()
        return self.zoho

    def detail(self, token: str) -> PolicyDetail:
        context = unsign_record_context(token, "policy")
        detail, _members = self.group(token, source_kind=context.get("source_kind"))
        return detail

    def _build_detail(
        self,
        token: str,
        policy: dict[str, object],
        relations: tuple[dict[str, object], ...],
        truncated: bool,
        risks: tuple[RelatedRisk, ...],
        source_kind: str,
        source_contact: dict[str, object],
    ) -> PolicyDetail:
        branch = classify_branch(policy.get("Ramo"))
        layout_name, layout_category = _layout(policy.get("Layout"))
        insured = tuple(self._insured(item) for item in relations)
        warnings: list[str] = []
        if branch is None:
            warnings.append("El ramo todavía no tiene una configuración funcional confirmada.")
        if truncated:
            raise ColectivosServiceError(
                "group_too_large",
                "El grupo supera el máximo operativo configurado y no se publicará incompleto.",
            )
        states = [str(item.get("Estado") or "").casefold() for item in relations]
        if any(self._is_negative(item.get("Pago_total")) for item in relations):
            warnings.append("Algunos valores provienen del registro actual y pueden requerir confirmación.")
        calendar = tuple(
            (f"Cuota {index}", _text(policy.get(f"Pago_{index}")))
            for index in range(1, 13) if policy.get(f"Pago_{index}") not in (None, "")
        )
        plan_values = tuple(sorted({
            _text(item.get("Plan"))
            for item in relations
            if item.get("Plan") not in (None, "")
        } | {
            _text(policy.get(field)) for field in ("Plan", "Referencia_Plan")
            if policy.get(field) not in (None, "")
        }, key=str.casefold))
        economic_values = []
        for api_name, label in (
            ("Valor_prima", "Prima de póliza sin IVA"),
            ("Pago_total", "Pago total de póliza"),
            ("Valor_asegurado", "Valor asegurado de póliza"),
        ):
            if policy.get(api_name) not in (None, ""):
                economic_values.append((label, _text(policy.get(api_name))))
        for api_name, label in (
            ("Prima", "Prima"),
            ("Pago_total", "Pago total"),
            ("Pago_total_Seg_n_la_forma_de_pago_Valor_asegura", "Pago según forma de pago"),
            ("Pago_EMPLEADO_Sin_IVA", "Pago empleado sin IVA"),
            ("Valor_asegurado", "Valor asegurado"),
        ):
            values = tuple(dict.fromkeys(
                _text(item.get(api_name))
                for item in relations
                if item.get(api_name) not in (None, "")
            ))
            if values:
                visible = " · ".join(values[:10])
                if len(values) > 10:
                    visible += f" · {len(values) - 10} valores adicionales"
                economic_values.append((label, visible))
        return PolicyDetail(
            detail_token=token,
            masked_reference=mask_reference(policy.get("Name")),
            branch_code=branch.code if branch else "",
            branch_name=branch.name if branch else _text(policy.get("Ramo"), "Ramo pendiente de clasificación"),
            classification="confirmed" if branch else "unknown",
            insurer=_text(policy.get("Aseguradora1"), "Sin aseguradora"),
            state=_text(policy.get("Estado_de_la_p_liza"), "Sin estado"),
            holder=_text(policy.get("Tomador_principal1"), "Sin tomador"),
            start_date=_text(policy.get("P_liza_Fecha_de_inicio_vigencia")),
            end_date=_text(policy.get("P_liza_Fecha_fin_de_la_vigencia")),
            renewable=_text(policy.get("Renovable"), "Sin información"),
            payment_mode=_text(policy.get("Modo_de_pago"), "Sin información"),
            frequency=_text(policy.get("Frecuencia"), "Sin información"),
            installments=_text(policy.get("N_mero_de_cuotas"), "Sin información"),
            first_installment_date=_text(policy.get("Fecha_primera_cuota")),
            payment_calendar=calendar,
            insured=insured,
            risks=risks,
            active_count=sum("activo" in state for state in states),
            excluded_count=sum("exclu" in state for state in states),
            retired_count=sum("retir" in state for state in states),
            affiliate_count=sum(
                bool(_lookup_id(item.get("Contacto_facturaci_n_dividida_colectivas")))
                for item in relations
            ),
            beneficiary_count=sum(
                bool(_lookup_id(item.get("Beneficiario"))) for item in relations
            ),
            plan_values=plan_values,
            economic_values=tuple(economic_values),
            warnings=tuple(warnings),
            truncated=truncated,
            full_reference=_text(policy.get("Name")),
            layout_name=layout_name,
            layout_category=layout_category,
            source_kind=source_kind,
            source_name=_text(
                source_contact.get("Nombre_comercial")
                or source_contact.get("Raz_n_social")
                or source_contact.get("Full_Name")
            ),
            source_summary=(
                ContactSummary(
                    person_type=_text(source_contact.get("Tipo_de_persona")),
                    id_type=_text(source_contact.get("Tipo_ID")),
                    masked_document=mask_document(source_contact.get("N_mero_de_ID")),
                    state=_text(source_contact.get("Estado"), "Sin estado"),
                    email=_text(source_contact.get("Email")),
                    phone=_text(source_contact.get("Phone")),
                    mobile=_text(source_contact.get("Mobile")),
                    city=_text(source_contact.get("Ciudad_de_direcci_n_principal")),
                    address=_text(source_contact.get("Direcci_n")),
                ) if source_contact else None
            ),
            payment_method=_text(policy.get("Medio_de_pago")),
            payment_periodicity=_text(policy.get("Periodicidad_de_pago")),
        )

    def group(self, token: str, *, source_kind: str | None = None, refresh: bool = False) -> tuple[PolicyDetail, tuple[GroupMember, ...]]:
        total_started = time.monotonic()
        if self.use_preparation:
            if refresh:
                invalidate_policy_preparation(
                    token=token, profile=self.profile, backend=self.backend,
                    source_kind=source_kind,
                )
                self.preparation_status = "refresh_manual"
            else:
                validation_started = time.monotonic()
                load_status = {}
                cached = load_policy_preparation(
                    token=token, profile=self.profile, backend=self.backend,
                    source_kind=source_kind, status_out=load_status,
                )
                self.timings["snapshot_validation_ms"] = round((time.monotonic() - validation_started) * 1000)
                if cached is not None:
                    detail, members, metadata = cached
                    self.last_detail, self.last_members = detail, members
                    self.preparation_status = "hit"
                    self.preparation_metadata = metadata
                    self._log_group(total_started, len(members), "hit")
                    return detail, members
                self.preparation_status = load_status.get("status", "miss")
                if self.preparation_status == "invalid":
                    raise ColectivosServiceError(
                        "invalid_response",
                        "El Workspace local no es válido. Actualice la información desde Zoho.",
                    )
        self._ensure_zoho()
        policy_id = unsign_record_id(token, "policy")
        started = time.monotonic()
        policy = self._policy(policy_id)
        self.timings["policy_lookup_ms"] = round((time.monotonic() - started) * 1000)
        started = time.monotonic()
        relations, truncated = self._relations(policy_id)
        self.timings["risks1_query_ms"] = round((time.monotonic() - started) * 1000)
        if source_kind == "person":
            source_id = str(unsign_record_context(token, "policy").get("source_id") or "")
            if not ZOHO_ID.fullmatch(source_id):
                raise ColectivosServiceError("invalid_record", "La relación de la persona no es válida.")
            relations = tuple(
                relation for relation in relations
                if any(
                    _lookup_id(relation.get(field)) == source_id
                    for field, _label in INSURED_CONTACT_ROLES
                )
            )
        token_context = unsign_record_context(token, "policy")
        source_id = str(token_context.get("source_id") or "")
        contact_ids = {
            lookup_id for relation in relations
            for field, _label in INSURED_CONTACT_ROLES
            if (lookup_id := _lookup_id(relation.get(field)))
        }
        if ZOHO_ID.fullmatch(source_id):
            contact_ids.add(source_id)
        started = time.monotonic()
        contacts = self._batch("Contacts", CONTACT_BATCH_FIELDS, contact_ids)
        self.timings["contacts_query_ms"] = round((time.monotonic() - started) * 1000)
        risk_ids = {_lookup_id(item.get("Riesgo")) for item in relations}
        risk_ids.discard("")
        started = time.monotonic()
        risks = self._batch(RISKS_MODULE, RISK_DETAIL_FIELDS, risk_ids)
        self.timings["risks_query_ms"] = round((time.monotonic() - started) * 1000)
        dto_started = time.monotonic()
        risk_dtos = self._risk_dtos(risks)
        detail = self._build_detail(
            token, policy, relations, truncated, risk_dtos,
            source_kind or str(token_context.get("source_kind") or ""),
            contacts.get(source_id, {}),
        )
        self.timings["dto_ms"] = round((time.monotonic() - dto_started) * 1000)
        grouping_started = time.monotonic()
        members: list[GroupMember] = []
        for relation in relations:
            roles = [(field, label) for field, label in INSURED_CONTACT_ROLES if _lookup_id(relation.get(field))]
            if not roles:
                roles = [("", "Registro relacionado")]
            role_contacts = {
                label: contacts.get(_lookup_id(relation.get(field)), {})
                for field, label in INSURED_CONTACT_ROLES
            }
            associate = role_contacts.get("Afiliado", {})
            insured = role_contacts.get("Asegurado", {})
            beneficiary = role_contacts.get("Beneficiario", {})
            for field, role in roles:
                contact = contacts.get(_lookup_id(relation.get(field)), {})
                risk = risks.get(_lookup_id(relation.get("Riesgo")), {})
                economics = tuple(
                    (label, _text(relation.get(api_name))) for api_name, label in (
                        ("Prima", "Prima"), ("Pago_total", "Pago total"),
                        ("Pago_total_Seg_n_la_forma_de_pago_Valor_asegura", "Pago según forma de pago"),
                        ("Pago_EMPLEADO_Sin_IVA", "Pago empleado sin IVA"),
                        ("Valor_asegurado", "Valor asegurado"),
                    ) if relation.get(api_name) not in (None, "")
                )
                members.append(GroupMember(
                    role=role,
                    display_name=_text(contact.get("Full_Name") or (relation.get(field) if field else None), "Información protegida"),
                    id_type=_text(contact.get("Tipo_ID")),
                    masked_document=mask_document(contact.get("N_mero_de_ID")),
                    document=_text(contact.get("N_mero_de_ID")),
                    state=_text(relation.get("Estado"), "Sin estado"),
                    entry_date=_text(relation.get("Fecha_ingreso_riesgo")),
                    exit_date=_text(relation.get("Fecha_salida_riesgo")),
                    plan=_text(relation.get("Plan")),
                    relationship=_text(relation.get("Parentesco")),
                    risk_summary=_text(risk.get("Tipo_de_riesgo") or risk.get("Name")),
                    risk_attributes=tuple(
                        (key, _text(risk.get(field)))
                        for field, key in (
                            ("Ciudad", "ciudad"), ("Direccion", "direccion"),
                            ("A_o_construcci_n", "anio_construccion"),
                            ("Tipo_de_uso", "tipo_uso"), ("Name", "vehiculo"),
                            ("Placa_del_vehiculo", "placa"),
                            ("Marca_Tipo_Caracter_sticas", "marca"), ("Modelo", "modelo"),
                        )
                        if risk.get(field) not in (None, "")
                    ),
                    email=_text(contact.get("Email") or relation.get("Correo_electr_nico_afiliado") or relation.get("Email")),
                    phone=_text(contact.get("Phone")),
                    mobile=_text(contact.get("Mobile")),
                    economic_values=economics,
                    associate_name=_text(associate.get("Full_Name")),
                    associate_id_type=_text(associate.get("Tipo_ID")),
                    associate_document=_text(associate.get("N_mero_de_ID")),
                    associate_masked_document=mask_document(associate.get("N_mero_de_ID")),
                    insured_name=_text(insured.get("Full_Name")),
                    insured_id_type=_text(insured.get("Tipo_ID")),
                    insured_document=_text(insured.get("N_mero_de_ID")),
                    insured_masked_document=mask_document(insured.get("N_mero_de_ID")),
                    beneficiary_name=_text(beneficiary.get("Full_Name")),
                    beneficiary_id_type=_text(beneficiary.get("Tipo_ID")),
                    beneficiary_document=_text(beneficiary.get("N_mero_de_ID")),
                    beneficiary_masked_document=mask_document(beneficiary.get("N_mero_de_ID")),
                    associate_key=functional_reference(_lookup_id(relation.get("Contacto_facturaci_n_dividida_colectivas")), "contact"),
                    insured_key=functional_reference(_lookup_id(relation.get("Asegurado")), "contact"),
                    beneficiary_key=functional_reference(_lookup_id(relation.get("Beneficiario")), "contact"),
                    risk_key=functional_reference(_lookup_id(relation.get("Riesgo")), "risk"),
                ))
        result = tuple(members)
        self.timings["grouping_ms"] = round((time.monotonic() - grouping_started) * 1000)
        self.timings["total_ms"] = round((time.monotonic() - total_started) * 1000)
        load_status = self.preparation_status
        if self.use_preparation:
            self.preparation_metadata = store_policy_preparation(
                token=token, profile=self.profile, backend=self.backend,
                source_kind=source_kind, detail=detail, members=result,
                actor="manual_refresh" if refresh else "initial_load",
                timings=self.timings,
            )
            self.preparation_metadata["load_status"] = load_status
            self.preparation_status = "refresh_manual" if refresh else "miss"
        log_status = load_status if load_status in {"expired", "invalid"} else self.preparation_status
        self._log_group(total_started, len(result), log_status)
        self.last_detail, self.last_members = detail, result
        return detail, result

    def _log_group(self, started: float, count: int, cache_status: str) -> None:
        logger.info(
            "colectivos_policy_preparation application=cotizacion_colectivos operation=policy_group "
            "profile=%s backend=%s cache=%s records=%d total_ms=%d "
            "profile_resolution_ms=%d facade_ms=%d organization_ms=%d policy_lookup_ms=%d policy_search_ms=%d risks1_query_ms=%d contacts_query_ms=%d "
            "risks_query_ms=%d dto_ms=%d grouping_ms=%d snapshot_validation_ms=%d "
            "snapshot_serialization_ms=%d encryption_ms=%d workspace_persistence_ms=%d "
            "remote_queries=%d records_queries=%d search_queries=%d coql_queries=%d insured_pages=%d contacts_batches=%d risks_batches=%d",
            self.profile, self.backend, cache_status, count,
            round((time.monotonic() - started) * 1000),
            self.timings.get("profile_resolution_ms", 0), self.timings.get("facade_ms", 0),
            self.timings.get("organization_ms", 0), self.timings.get("policy_lookup_ms", 0),
            self.timings.get("policy_search_ms", 0),
            self.timings.get("risks1_query_ms", 0), self.timings.get("contacts_query_ms", 0),
            self.timings.get("risks_query_ms", 0), self.timings.get("dto_ms", 0),
            self.timings.get("grouping_ms", 0),
            self.timings.get("snapshot_validation_ms", 0), self.timings.get("snapshot_serialization_ms", 0),
            self.timings.get("encryption_ms", 0), self.timings.get("workspace_persistence_ms", 0),
            self.timings.get("remote_queries", 0), self.timings.get("records_queries", 0),
            self.timings.get("search_queries", 0), self.timings.get("coql_queries", 0),
            self.timings.get("insured_pages", 0), self.timings.get("contacts_batches", 0),
            self.timings.get("risks_batches", 0),
        )

    def _log_remote_error(self, exc: ZohoError, *, operation: str, fallback: str) -> None:
        raw_code = str(getattr(exc, "code", "") or "").strip().upper()
        code = raw_code if SAFE_ERROR_CODE.fullmatch(raw_code) else "UNAVAILABLE"
        logger.warning(
            "colectivos_remote_error application=cotizacion_colectivos profile=%s backend=%s "
            "operation=%s category=%s code=%s fallback=%s retry=facade_managed",
            self.profile, self.backend, operation, type(exc).__name__, code, fallback,
        )

    def _policy(self, policy_id: str) -> dict[str, object]:
        """Read the custom policy module through its stable facade operation."""
        self._ensure_zoho()
        started = time.monotonic()
        try:
            self.timings["search_queries"] += 1
            self.timings["remote_queries"] += 1
            page = self.zoho.search.by_criteria(
                module=POLICIES_MODULE,
                criteria=f"(id:equals:{escape_criteria_value(policy_id)})",
                fields=POLICY_DETAIL_FIELDS,
                page=1,
                limit=1,
            )
        except ZohoError as exc:
            self._log_remote_error(exc, operation="policy_search", fallback="none")
            raise translate_zoho_error(exc, self.profile) from exc
        finally:
            self.timings["policy_search_ms"] += round(
                (time.monotonic() - started) * 1000
            )
        if not page.records:
            raise ColectivosServiceError("not_found", "La póliza solicitada no existe.")
        return page.records[0]

    def _relations(self, policy_id: str) -> tuple[tuple[dict[str, object], ...], bool]:
        self._ensure_zoho()
        records = []
        page_number = 1
        more = True
        try:
            while more and len(records) < POLICY_GROUP_LIMIT:
                self.timings["search_queries"] += 1
                self.timings["remote_queries"] += 1
                page = self.zoho.search.by_criteria(
                    module=INSURED_MODULE,
                    criteria=f"(P_liza:equals:{escape_criteria_value(policy_id)})",
                    fields=INSURED_RELATION_FIELDS,
                    page=page_number,
                    limit=POLICY_GROUP_PAGE_SIZE,
                )
                self.timings["insured_pages"] += 1
                records.extend(page.records[: POLICY_GROUP_LIMIT - len(records)])
                more = bool(page.more_records)
                page_number += 1
        except ZohoError as exc:
            self._log_remote_error(exc, operation="insured_search", fallback="none")
            raise translate_zoho_error(exc, self.profile) from exc
        if more:
            raise ColectivosServiceError(
                "group_too_large",
                "El grupo supera el máximo operativo configurado y no se publicará incompleto.",
            )
        return tuple(records), False

    def _batch(self, module: str, fields: tuple[str, ...], ids: set[str]) -> dict[str, dict[str, object]]:
        if not ids:
            return {}
        self._ensure_zoho()
        result = {}
        ordered = sorted(ids)
        for offset in range(0, len(ordered), RELATED_BATCH_SIZE):
            chunk = set(ordered[offset : offset + RELATED_BATCH_SIZE])
            try:
                self.timings["coql_queries"] += 1
                self.timings["remote_queries"] += 1
                page = self.zoho.coql.execute(_fixed_batch_query(module, fields, chunk))
            except ZohoError as exc:
                self._log_remote_error(exc, operation=f"related_{module.casefold()}", fallback="omitted")
                continue
            metric = "contacts_batches" if module == "Contacts" else "risks_batches"
            self.timings[metric] += 1
            result.update({str(item.get("id")): item for item in page.records if ZOHO_ID.fullmatch(str(item.get("id") or ""))})
        return result

    @staticmethod
    def _is_negative(value: object) -> bool:
        try:
            return float(value) < 0
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _insured(record: dict[str, object]) -> RelatedInsured:
        roles = [label for field, label in INSURED_CONTACT_ROLES if _lookup_id(record.get(field))]
        return RelatedInsured(
            masked_reference=mask_reference(record.get("Name")), state=_text(record.get("Estado"), "Sin estado"),
            branch=_text(record.get("Ramo"), "Sin ramo"), insurer=_text(record.get("Aseguradora"), "Sin aseguradora"),
            entry_date=_text(record.get("Fecha_ingreso_riesgo")), exit_date=_text(record.get("Fecha_salida_riesgo")),
            policy_reference=mask_reference(_text(record.get("P_liza"))), has_risk=bool(_lookup_id(record.get("Riesgo"))),
            role=" / ".join(roles) or "Registro relacionado", plan=_text(record.get("Plan")),
        )

    @staticmethod
    def _risk_dtos(records: dict[str, dict[str, object]]) -> tuple[RelatedRisk, ...]:
        result = []
        for record in records.values():
            attributes = tuple((label, _text(record.get(field))) for field, label in (("Ciudad", "Ciudad"), ("Direccion", "Dirección"), ("Tipo_de_uso", "Tipo de uso"), ("Marca_Tipo_Caracter_sticas", "Marca"), ("Modelo", "Modelo")) if record.get(field) not in (None, ""))
            result.append(RelatedRisk(masked_reference=mask_reference(record.get("Name")), state="", risk_type=_text(record.get("Tipo_de_riesgo"), "Sin tipo"), start_date=_text(record.get("Fecha_inicio")), end_date=_text(record.get("Fecha_fin")), attributes=attributes))
        return tuple(result)
