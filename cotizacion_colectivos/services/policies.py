from __future__ import annotations

from cotizacion_colectivos.branches import classify_branch
from cotizacion_colectivos.dto import GroupMember, PolicyDetail, RelatedInsured, RelatedRisk
from integrations.zoho.exceptions import ZohoError

from .common import (
    ColectivosServiceError,
    ZOHO_ID,
    colectivos_zoho,
    escape_criteria_value,
    get_colectivos_profile,
    mask_document,
    mask_reference,
    sign_record_id,
    translate_zoho_error,
    unsign_record_id,
)
from .entity_detail import _lookup_id, _text
from .mappings import (
    INSURED_MODULE,
    INSURED_RELATION_FIELDS,
    POLICIES_MODULE,
    POLICY_DETAIL_FIELDS,
    RISKS_MODULE,
    RISK_DETAIL_FIELDS,
)

POLICY_GROUP_LIMIT = 200
CONTACT_BATCH_FIELDS = ("id", "Tipo_ID", "N_mero_de_ID", "Full_Name")


def _fixed_batch_query(module: str, fields: tuple[str, ...], ids: set[str]) -> str:
    if module not in {"Contacts", RISKS_MODULE} or not ids or any(not ZOHO_ID.fullmatch(value) for value in ids):
        raise ColectivosServiceError("invalid_response", "No fue posible consultar la información relacionada.")
    safe_ids = ",".join(f"'{value}'" for value in sorted(ids))
    return f"select {','.join(fields)} from {module} where id in ({safe_ids}) limit {POLICY_GROUP_LIMIT}"


class PolicyService:
    def __init__(self, zoho=None):
        self.profile = get_colectivos_profile()
        try:
            self.zoho = zoho or colectivos_zoho()
        except ZohoError as exc:
            raise translate_zoho_error(exc, self.profile) from exc

    def detail(self, token: str) -> PolicyDetail:
        policy_id = unsign_record_id(token, "policy")
        policy = self._policy(policy_id)
        relations, truncated = self._relations(policy_id)
        return self._build_detail(token, policy, relations, truncated)

    def _build_detail(
        self,
        token: str,
        policy: dict[str, object],
        relations: tuple[dict[str, object], ...],
        truncated: bool,
    ) -> PolicyDetail:
        branch = classify_branch(policy.get("Ramo"))
        insured = tuple(self._insured(item) for item in relations)
        risks = self._risks(relations)
        warnings: list[str] = []
        if branch is None:
            warnings.append("Ramo pendiente de clasificación; no se pueden crear solicitudes.")
        if truncated:
            warnings.append("El grupo alcanzó el límite defensivo de 200 registros; el resultado es parcial.")
        states = [str(item.get("Estado") or "").casefold() for item in relations]
        if any(self._is_negative(item.get("Pago_total")) for item in relations):
            warnings.append("Se detectaron valores económicos que requieren revisión interna.")
        calendar = tuple(
            (f"Cuota {index}", _text(policy.get(f"Pago_{index}")))
            for index in range(1, 13) if policy.get(f"Pago_{index}") not in (None, "")
        )
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
            excluded_count=sum("exclu" in state or "retir" in state for state in states),
            warnings=tuple(warnings),
            truncated=truncated,
        )

    def group(self, token: str) -> tuple[PolicyDetail, tuple[GroupMember, ...]]:
        policy_id = unsign_record_id(token, "policy")
        policy = self._policy(policy_id)
        relations, truncated = self._relations(policy_id)
        detail = self._build_detail(token, policy, relations, truncated)
        contact_ids = {
            lookup_id for relation in relations
            for field in ("Asegurado", "Afiliado", "Beneficiario")
            if (lookup_id := _lookup_id(relation.get(field)))
        }
        contacts = self._batch("Contacts", CONTACT_BATCH_FIELDS, contact_ids)
        risk_ids = {_lookup_id(item.get("Riesgo")) for item in relations}
        risk_ids.discard("")
        risks = self._batch(RISKS_MODULE, RISK_DETAIL_FIELDS, risk_ids)
        members: list[GroupMember] = []
        for relation in relations:
            roles = [(field, label) for field, label in (("Asegurado", "Asegurado"), ("Afiliado", "Afiliado"), ("Beneficiario", "Beneficiario")) if _lookup_id(relation.get(field))]
            if not roles:
                roles = [("", "Registro relacionado")]
            for field, role in roles:
                contact = contacts.get(_lookup_id(relation.get(field)), {})
                risk = risks.get(_lookup_id(relation.get("Riesgo")), {})
                economics = tuple(
                    (label, _text(relation.get(api_name))) for api_name, label in (
                        ("Prima", "Prima"), ("Pago_total", "Pago total"),
                        ("Pago_total_Seg_n_la_forma_de_pago_Valor_asegura", "Pago según forma de pago"),
                        ("Pago_EMPLEADO_Sin_IVA", "Pago empleado sin IVA"),
                    ) if relation.get(api_name) not in (None, "")
                )
                members.append(GroupMember(
                    role=role,
                    display_name=_text(contact.get("Full_Name") or (relation.get(field) if field else None), "Información protegida"),
                    id_type=_text(contact.get("Tipo_ID")),
                    masked_document=mask_document(contact.get("N_mero_de_ID")),
                    state=_text(relation.get("Estado"), "Sin estado"),
                    entry_date=_text(relation.get("Fecha_ingreso_riesgo")),
                    exit_date=_text(relation.get("Fecha_salida_riesgo")),
                    plan=_text(relation.get("Plan")),
                    relationship=_text(relation.get("Parentesco")),
                    risk_summary=_text(risk.get("Tipo_de_riesgo") or risk.get("Name")),
                    economic_values=economics,
                ))
        return detail, tuple(members)

    def _policy(self, policy_id: str) -> dict[str, object]:
        try:
            return self.zoho.records.get_by_id(module=POLICIES_MODULE, record_id=policy_id, fields=POLICY_DETAIL_FIELDS)
        except ZohoError:
            try:
                page = self.zoho.search.by_criteria(module=POLICIES_MODULE, criteria=f"(id:equals:{escape_criteria_value(policy_id)})", fields=POLICY_DETAIL_FIELDS, page=1, limit=1)
            except ZohoError as exc:
                raise translate_zoho_error(exc, self.profile) from exc
            if not page.records:
                raise ColectivosServiceError("not_found", "La póliza solicitada no existe.")
            return page.records[0]

    def _relations(self, policy_id: str) -> tuple[tuple[dict[str, object], ...], bool]:
        try:
            page = self.zoho.search.by_criteria(module=INSURED_MODULE, criteria=f"(P_liza:equals:{escape_criteria_value(policy_id)})", fields=INSURED_RELATION_FIELDS, page=1, limit=POLICY_GROUP_LIMIT)
        except ZohoError as exc:
            raise translate_zoho_error(exc, self.profile) from exc
        return tuple(page.records[:POLICY_GROUP_LIMIT]), bool(page.more_records)

    def _batch(self, module: str, fields: tuple[str, ...], ids: set[str]) -> dict[str, dict[str, object]]:
        if not ids:
            return {}
        try:
            page = self.zoho.coql.execute(_fixed_batch_query(module, fields, ids))
        except ZohoError:
            return {}
        return {str(item.get("id")): item for item in page.records if ZOHO_ID.fullmatch(str(item.get("id") or ""))}

    @staticmethod
    def _is_negative(value: object) -> bool:
        try:
            return float(value) < 0
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _insured(record: dict[str, object]) -> RelatedInsured:
        roles = [label for field, label in (("Asegurado", "Asegurado"), ("Afiliado", "Afiliado"), ("Beneficiario", "Beneficiario")) if _lookup_id(record.get(field))]
        return RelatedInsured(
            masked_reference=mask_reference(record.get("Name")), state=_text(record.get("Estado"), "Sin estado"),
            branch=_text(record.get("Ramo"), "Sin ramo"), insurer=_text(record.get("Aseguradora"), "Sin aseguradora"),
            entry_date=_text(record.get("Fecha_ingreso_riesgo")), exit_date=_text(record.get("Fecha_salida_riesgo")),
            policy_reference=mask_reference(_text(record.get("P_liza"))), has_risk=bool(_lookup_id(record.get("Riesgo"))),
            role=" / ".join(roles) or "Registro relacionado", plan=_text(record.get("Plan")),
        )

    def _risks(self, relations: tuple[dict[str, object], ...]) -> tuple[RelatedRisk, ...]:
        ids = {_lookup_id(item.get("Riesgo")) for item in relations}
        ids.discard("")
        records = self._batch(RISKS_MODULE, RISK_DETAIL_FIELDS, ids)
        result = []
        for record in records.values():
            attributes = tuple((label, _text(record.get(field))) for field, label in (("Ciudad", "Ciudad"), ("Direccion", "Dirección"), ("Tipo_de_uso", "Tipo de uso"), ("Marca_Tipo_Caracter_sticas", "Marca"), ("Modelo", "Modelo")) if record.get(field) not in (None, ""))
            result.append(RelatedRisk(masked_reference=mask_reference(record.get("Name")), state="", risk_type=_text(record.get("Tipo_de_riesgo"), "Sin tipo"), start_date=_text(record.get("Fecha_inicio")), end_date=_text(record.get("Fecha_fin")), attributes=attributes))
        return tuple(result)
