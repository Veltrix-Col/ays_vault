from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CompanySearchResult:
    detail_token: str
    display_name: str
    masked_document: str
    state: str


@dataclass(frozen=True)
class PersonSearchResult:
    detail_token: str
    full_name: str
    masked_document: str
    state: str


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
    warnings: tuple[str, ...] = ()
    truncated: bool = False


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
    economic_values: tuple[tuple[str, str], ...] = ()


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
