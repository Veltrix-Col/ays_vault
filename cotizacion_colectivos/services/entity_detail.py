from __future__ import annotations

from cotizacion_colectivos.dto import (
    CompanyDetail,
    ContactSummary,
    PersonDetail,
    RelatedInsured,
    RelatedPolicy,
    RelatedRisk,
)
from integrations.zoho.exceptions import ZohoError

from .common import (
    ColectivosServiceError,
    ZOHO_ID,
    escape_criteria_value,
    mask_document,
    mask_reference,
    colectivos_zoho,
    get_colectivos_profile,
    translate_zoho_error,
    unsign_record_id,
)
from .mappings import (
    COMPANY_ID_TYPE,
    COMPANY_TYPE,
    CONFIRMED_RELATIONS,
    CONTACT_DETAIL_FIELDS,
    CONTACTS_MODULE,
    INSURED_MODULE,
    INSURED_RELATION_FIELDS,
    PERSON_ID_TYPE,
    PERSON_TYPE,
    POLICIES_MODULE,
    POLICY_DETAIL_FIELDS,
    RELATION_LIMIT,
    RISKS_MODULE,
    RISK_DETAIL_FIELDS,
)


def _text(value: object, default: str = "") -> str:
    if isinstance(value, dict):
        value = value.get("name") or value.get("display_label") or value.get("value")
    clean = str(value or "").strip()
    return clean or default


def _lookup_id(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    candidate = str(value.get("id") or "").strip()
    return candidate if ZOHO_ID.fullmatch(candidate) else ""


def _layout(value: object) -> tuple[str, str]:
    name = _text(value)
    folded = name.casefold()
    category = "collective" if "colectiv" in folded else ("other" if name else "unknown")
    return name, category


class EntityDetailService:
    def __init__(self, zoho=None):
        self.profile = get_colectivos_profile()
        try:
            self.zoho = zoho or colectivos_zoho()
        except ZohoError as exc:
            raise translate_zoho_error(exc, self.profile) from exc

    def company(self, token: str) -> CompanyDetail:
        record = self._contact(token, COMPANY_TYPE, COMPANY_ID_TYPE)
        insured, policies, risks, unavailable, truncated = self._confirmed_relations(record["id"])
        direct, direct_unavailable, direct_truncated = self._direct_policies(record["id"])
        return CompanyDetail(
            display_name=_text(record.get("Nombre_comercial") or record.get("Raz_n_social"), "Empresa sin nombre"),
            legal_name=_text(record.get("Raz_n_social"), "Sin razón social"),
            id_type=_text(record.get("Tipo_ID"), "NIT"),
            masked_document=mask_document(record.get("N_mero_de_ID")),
            state=_text(record.get("Estado"), "Sin estado"),
            summary=self._summary(record),
            policies=policies,
            direct_policies=direct,
            insured=insured,
            risks=risks,
            unavailable_relations=tuple(dict.fromkeys((*unavailable, *direct_unavailable))),
            relations_truncated=truncated or direct_truncated,
        )

    def person(self, token: str) -> PersonDetail:
        record = self._contact(token, PERSON_TYPE, PERSON_ID_TYPE)
        insured, policies, risks, unavailable, truncated = self._confirmed_relations(record["id"])
        direct, direct_unavailable, direct_truncated = self._direct_policies(record["id"])
        return PersonDetail(
            full_name=_text(record.get("Full_Name"), "Persona sin nombre"),
            first_name=_text(record.get("First_Name")),
            last_name=_text(record.get("Last_Name")),
            id_type=_text(record.get("Tipo_ID"), "CC"),
            masked_document=mask_document(record.get("N_mero_de_ID")),
            state=_text(record.get("Estado"), "Sin estado"),
            summary=self._summary(record),
            company_name=_text(record.get("Empresa")),
            policies=policies,
            direct_policies=direct,
            insured=insured,
            risks=risks,
            unavailable_relations=tuple(dict.fromkeys((*unavailable, *direct_unavailable))),
            relations_truncated=truncated or direct_truncated,
        )

    @staticmethod
    def _summary(record) -> ContactSummary:
        return ContactSummary(
            person_type=_text(record.get("Tipo_de_persona")),
            id_type=_text(record.get("Tipo_ID")),
            masked_document=mask_document(record.get("N_mero_de_ID")),
            state=_text(record.get("Estado"), "Sin estado"),
            email=_text(record.get("Email")),
            phone=_text(record.get("Phone")),
            mobile=_text(record.get("Mobile")),
            city=_text(record.get("Ciudad_de_direcci_n_principal")),
            address=_text(record.get("Direcci_n")),
        )

    def _contact(self, token: str, person_type: str, id_type: str):
        record_id = unsign_record_id(token)
        try:
            record = self.zoho.records.get_by_id(
                module=CONTACTS_MODULE,
                record_id=record_id,
                fields=CONTACT_DETAIL_FIELDS,
            )
        except ZohoError:
            # El SDK oficial puede fallar al resolver el detalle aunque Search
            # esté disponible. El fallback sigue siendo cerrado: mismo módulo,
            # ID firmado validado y lista fija de campos.
            try:
                page = self.zoho.search.by_criteria(
                    module=CONTACTS_MODULE,
                    criteria=f"(id:equals:{escape_criteria_value(record_id)})",
                    fields=CONTACT_DETAIL_FIELDS,
                    page=1,
                    limit=1,
                )
            except ZohoError as exc:
                raise translate_zoho_error(exc, self.profile) from exc
            if not page.records:
                raise ColectivosServiceError("not_found", "El registro solicitado no existe.")
            record = page.records[0]
        if record.get("Tipo_de_persona") != person_type or record.get("Tipo_ID") != id_type:
            raise ColectivosServiceError("not_found", "El registro solicitado no existe.")
        return record

    def _confirmed_relations(self, contact_id: object):
        if "Riesgos1.Asegurado->Contacts" not in CONFIRMED_RELATIONS:
            return (), (), (), (), False
        try:
            page = self.zoho.search.by_criteria(
                module=INSURED_MODULE,
                criteria=f"(Asegurado:equals:{escape_criteria_value(str(contact_id))})",
                fields=INSURED_RELATION_FIELDS,
                page=1,
                limit=RELATION_LIMIT,
            )
        except ZohoError:
            return (), (), (), ("Asegurados, pólizas y riesgos no están disponibles temporalmente.",), False

        insured: list[RelatedInsured] = []
        policies: list[RelatedPolicy] = []
        risks: list[RelatedRisk] = []
        policy_ids: set[str] = set()
        risk_ids: set[str] = set()
        unavailable: list[str] = []
        for relation in page.records[:RELATION_LIMIT]:
            policy_lookup = relation.get("P_liza")
            risk_lookup = relation.get("Riesgo")
            insured.append(self._insured(relation, policy_lookup, risk_lookup))

            policy_id = _lookup_id(policy_lookup)
            if policy_id and policy_id not in policy_ids:
                policy_ids.add(policy_id)
                policy, failed = self._policy_by_id(policy_id, relation)
                policies.append(policy)
                if failed:
                    unavailable.append("Parte del detalle de pólizas no está disponible temporalmente.")

            risk_id = _lookup_id(risk_lookup)
            if risk_id and risk_id not in risk_ids:
                risk_ids.add(risk_id)
                risk, failed = self._risk_by_id(risk_id, relation)
                risks.append(risk)
                if failed:
                    unavailable.append("Parte del detalle de riesgos no está disponible temporalmente.")

        policies.sort(key=lambda item: (item.layout_category != "collective", item.masked_reference))
        return tuple(insured), tuple(policies), tuple(risks), tuple(dict.fromkeys(unavailable)), bool(page.more_records)

    @staticmethod
    def _insured(record, policy_lookup, risk_lookup) -> RelatedInsured:
        return RelatedInsured(
            masked_reference=mask_reference(record.get("Name")),
            state=_text(record.get("Estado"), "Sin estado"),
            branch=_text(record.get("Ramo"), "Sin ramo"),
            insurer=_text(record.get("Aseguradora"), "Sin aseguradora"),
            entry_date=_text(record.get("Fecha_ingreso_riesgo")),
            exit_date=_text(record.get("Fecha_salida_riesgo")),
            policy_reference=mask_reference(policy_lookup.get("name")) if isinstance(policy_lookup, dict) else "",
            has_risk=bool(_lookup_id(risk_lookup)),
        )

    def _policy_by_id(self, policy_id: str, relation) -> tuple[RelatedPolicy, bool]:
        failed = False
        try:
            record = self.zoho.records.get_by_id(
                module=POLICIES_MODULE, record_id=policy_id, fields=POLICY_DETAIL_FIELDS
            )
        except ZohoError:
            record, failed = {}, True
        lookup = relation.get("P_liza")
        fallback_name = lookup.get("name") if isinstance(lookup, dict) else ""
        layout_name, layout_category = _layout(record.get("Layout"))
        return RelatedPolicy(
            masked_reference=mask_reference(record.get("Name") or fallback_name),
            state=_text(record.get("Estado_de_la_p_liza"), "Sin estado"),
            branch=_text(record.get("Ramo") or relation.get("Ramo"), "Sin ramo"),
            insurer=_text(record.get("Aseguradora1") or relation.get("Aseguradora"), "Sin aseguradora"),
            start_date=_text(record.get("P_liza_Fecha_de_inicio_vigencia")),
            end_date=_text(record.get("P_liza_Fecha_fin_de_la_vigencia")),
            layout_name=layout_name,
            layout_category=layout_category,
        ), failed

    def _risk_by_id(self, risk_id: str, relation) -> tuple[RelatedRisk, bool]:
        failed = False
        try:
            record = self.zoho.records.get_by_id(
                module=RISKS_MODULE, record_id=risk_id, fields=RISK_DETAIL_FIELDS
            )
        except ZohoError:
            record, failed = {}, True
        lookup = relation.get("Riesgo")
        fallback_name = lookup.get("name") if isinstance(lookup, dict) else ""
        return RelatedRisk(
            masked_reference=mask_reference(record.get("Name") or fallback_name),
            state=_text(relation.get("Estado"), "Sin estado"),
            risk_type=_text(record.get("Tipo_de_riesgo") or relation.get("Ramo"), "Sin tipo"),
            start_date=_text(record.get("Fecha_inicio")),
            end_date=_text(record.get("Fecha_fin")),
        ), failed

    def _direct_policies(self, contact_id: object):
        try:
            page = self.zoho.search.by_criteria(
                module=POLICIES_MODULE,
                criteria=f"(Tomador_principal1:equals:{escape_criteria_value(str(contact_id))})",
                fields=POLICY_DETAIL_FIELDS,
                page=1,
                limit=RELATION_LIMIT,
            )
        except ZohoError:
            return (), ("Las pólizas como tomador no están disponibles temporalmente.",), False
        policies: list[RelatedPolicy] = []
        seen: set[str] = set()
        for record in page.records[:RELATION_LIMIT]:
            record_id = str(record.get("id") or "")
            if not ZOHO_ID.fullmatch(record_id) or record_id in seen:
                continue
            seen.add(record_id)
            layout_name, layout_category = _layout(record.get("Layout"))
            policies.append(RelatedPolicy(
                masked_reference=mask_reference(record.get("Name")),
                state=_text(record.get("Estado_de_la_p_liza"), "Sin estado"),
                branch=_text(record.get("Ramo"), "Sin ramo"),
                insurer=_text(record.get("Aseguradora1"), "Sin aseguradora"),
                start_date=_text(record.get("P_liza_Fecha_de_inicio_vigencia")),
                end_date=_text(record.get("P_liza_Fecha_fin_de_la_vigencia")),
                layout_name=layout_name,
                layout_category=layout_category,
                relationship_source="direct_tomador",
                relationship_confidence="partial",
            ))
        policies.sort(key=lambda item: (item.layout_category != "collective", item.masked_reference))
        return tuple(policies), (), bool(page.more_records)
