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


@dataclass(frozen=True)
class RelatedRisk:
    masked_reference: str
    state: str
    risk_type: str
    start_date: str = ""
    end_date: str = ""
    relationship_source: str = "insured_risk"
    relationship_confidence: str = "confirmed"


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
