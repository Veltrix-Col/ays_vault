from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AccessToken:
    value: str = field(repr=False)
    expires_at: float
    api_domain: str


@dataclass(frozen=True)
class Organization:
    organization_id: str
    company_name: str
    country: str = ""
    timezone: str = ""
    currency: str = ""
    data_center: str = ""
    primary_email: str = ""
    environment: str = ""


@dataclass(frozen=True)
class ModuleMetadata:
    api_name: str
    module_name: str
    plural_label: str
    singular_label: str
    custom_module: bool = False
    generated_type: str = ""
    api_supported: bool = False
    status: str = ""


@dataclass(frozen=True)
class FieldMetadata:
    api_name: str
    field_label: str
    data_type: str
    required: bool = False
    read_only: bool = False
    custom_field: bool = False
    length: int | None = None
    pick_list_values: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    lookup: dict[str, Any] = field(default_factory=dict)
    related_details: dict[str, Any] = field(default_factory=dict)
    unique: bool = False
    system_mandatory: bool = False


@dataclass(frozen=True)
class Page:
    records: tuple[dict[str, Any], ...]
    more_records: bool = False
    page: int = 1
    count: int = 0
