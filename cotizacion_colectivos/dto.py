from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CompanySearchResult:
    detail_token: str
    display_name: str
    masked_document: str
    state: str
    document: str = ""


@dataclass(frozen=True)
class PersonSearchResult:
    detail_token: str
    full_name: str
    masked_document: str
    state: str
    document: str = ""


@dataclass(frozen=True)
class ClientSearchResult:
    detail_token: str
    source_kind: str
    display_name: str
    entity_label: str
    document_label: str
    masked_document: str
    state: str
    document: str = ""


@dataclass(frozen=True)
class RequestPolicyOption:
    detail_token: str
    masked_reference: str
    branch_code: str
    branch_name: str
    insurer: str
    state: str
    start_date: str = ""
    end_date: str = ""
    related_count: int = 0
    warnings: tuple[str, ...] = ()
    renewable: str = ""


@dataclass(frozen=True)
class ContactSummary:
    person_type: str
    id_type: str
    masked_document: str
    state: str
    email: str = ""
    phone: str = ""
    mobile: str = ""
    city: str = ""
    address: str = ""
    # Solo se persiste dentro del Workspace cifrado. Las vistas usan siempre
    # masked_document; los generadores autorizados pueden consumir el valor
    # exacto sin una nueva consulta remota.
    document: str = ""


@dataclass(frozen=True)
class RelatedPolicy:
    detail_token: str
    masked_reference: str
    state: str
    branch: str
    insurer: str
    start_date: str = ""
    end_date: str = ""
    layout_name: str = ""
    layout_category: str = "unknown"
    relationship_source: str = "insured"
    relationship_confidence: str = "confirmed"
    renewable: str = ""
    # El número de póliza es contexto operativo solicitado. Solo se presenta
    # después de resolver un cliente mediante un token firmado.
    full_reference: str = ""


@dataclass(frozen=True)
class RelatedInsured:
    masked_reference: str
    state: str
    branch: str
    insurer: str
    entry_date: str = ""
    exit_date: str = ""
    policy_reference: str = ""
    has_risk: bool = False
    relationship_source: str = "insured_contact"
    relationship_confidence: str = "confirmed"
    role: str = "Asegurado"
    plan: str = ""
    relationship_token: str = ""
    full_reference: str = ""
    full_policy_reference: str = ""


@dataclass(frozen=True)
class RelatedRisk:
    masked_reference: str
    state: str
    risk_type: str
    start_date: str = ""
    end_date: str = ""
    relationship_source: str = "insured_risk"
    relationship_confidence: str = "confirmed"
    attributes: tuple[tuple[str, str], ...] = ()
    full_reference: str = ""


@dataclass(frozen=True)
class BranchSummary:
    code: str
    slug: str
    name: str
    classification: str
    policies: tuple[RelatedPolicy, ...]
    insured_count: int
    risk_count: int
    active_count: int
    excluded_count: int
    roles: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class PolicyDetail:
    detail_token: str
    masked_reference: str
    branch_code: str
    branch_name: str
    classification: str
    insurer: str
    state: str
    holder: str
    start_date: str
    end_date: str
    renewable: str
    payment_mode: str
    frequency: str
    installments: str
    first_installment_date: str
    payment_calendar: tuple[tuple[str, str], ...]
    insured: tuple[RelatedInsured, ...]
    risks: tuple[RelatedRisk, ...]
    active_count: int
    excluded_count: int
    retired_count: int = 0
    affiliate_count: int = 0
    beneficiary_count: int = 0
    plan_values: tuple[str, ...] = ()
    economic_values: tuple[tuple[str, str], ...] = ()
    warnings: tuple[str, ...] = ()
    truncated: bool = False
    full_reference: str = ""
    layout_name: str = ""
    layout_category: str = "unknown"
    source_kind: str = ""
    source_name: str = ""
    source_summary: ContactSummary | None = None
    payment_method: str = ""
    payment_periodicity: str = ""


@dataclass(frozen=True)
class GroupMember:
    role: str
    display_name: str
    id_type: str
    masked_document: str
    state: str
    entry_date: str
    exit_date: str
    plan: str
    relationship: str
    risk_summary: str
    risk_attributes: tuple[tuple[str, str], ...] = ()
    document: str = ""
    email: str = ""
    phone: str = ""
    mobile: str = ""
    economic_values: tuple[tuple[str, str], ...] = ()
    associate_name: str = ""
    associate_id_type: str = ""
    associate_document: str = ""
    associate_masked_document: str = ""
    insured_name: str = ""
    insured_id_type: str = ""
    insured_document: str = ""
    insured_masked_document: str = ""
    beneficiary_name: str = ""
    beneficiary_id_type: str = ""
    beneficiary_document: str = ""
    beneficiary_masked_document: str = ""
    associate_key: str = ""
    insured_key: str = ""
    beneficiary_key: str = ""
    risk_key: str = ""
    # Structured Contact fields used when an existing affiliate is selected
    # as the requester of an Individual Quotation.
    first_name: str = ""
    last_name: str = ""
    birth_date: str = ""
    associate_first_name: str = ""
    associate_last_name: str = ""
    associate_birth_date: str = ""


@dataclass(frozen=True)
class CompanyDetail:
    display_name: str
    legal_name: str
    id_type: str
    masked_document: str
    state: str
    summary: ContactSummary
    policies: tuple[RelatedPolicy, ...]
    direct_policies: tuple[RelatedPolicy, ...]
    insured: tuple[RelatedInsured, ...]
    risks: tuple[RelatedRisk, ...]
    unavailable_relations: tuple[str, ...] = ()
    relations_truncated: bool = False
    branches: tuple[BranchSummary, ...] = ()
    document: str = ""


@dataclass(frozen=True)
class PersonDetail:
    full_name: str
    first_name: str
    last_name: str
    id_type: str
    masked_document: str
    state: str
    summary: ContactSummary
    company_name: str
    policies: tuple[RelatedPolicy, ...]
    direct_policies: tuple[RelatedPolicy, ...]
    insured: tuple[RelatedInsured, ...]
    risks: tuple[RelatedRisk, ...]
    unavailable_relations: tuple[str, ...] = ()
    relations_truncated: bool = False
    branches: tuple[BranchSummary, ...] = ()
    document: str = ""
