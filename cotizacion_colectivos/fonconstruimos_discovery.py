"""Discovery dirigido de la relación Fonconstruimos → empresas en Sandbox."""

from __future__ import annotations

from dataclasses import dataclass, field

from integrations.zoho.exceptions import ZohoError

from .task_contract_discovery import fingerprint


TARGET = "Fonconstruimos"
LIMIT = 5
SEARCHES = (
    ("Contacts", "Grupo_econ_mico", ("id", "Grupo_econ_mico", "Empresa")),
    ("Contacts", "Empresa", ("id", "Grupo_econ_mico", "Empresa")),
    (
        "Polizas", "Tomador_principal1",
        ("id", "Tomador_principal1", "Grupo_econ_mico", "Grupo_empresarial_ARL", "Vendedor"),
    ),
    (
        "Polizas", "Grupo_econ_mico",
        ("id", "Tomador_principal1", "Grupo_econ_mico", "Grupo_empresarial_ARL", "Vendedor"),
    ),
    (
        "Polizas", "Grupo_empresarial_ARL",
        ("id", "Tomador_principal1", "Grupo_econ_mico", "Grupo_empresarial_ARL", "Vendedor"),
    ),
    (
        "Polizas", "Vendedor",
        ("id", "Tomador_principal1", "Grupo_econ_mico", "Grupo_empresarial_ARL", "Vendedor"),
    ),
    ("Riesgos1", "Tomador", ("id", "Tomador", "Subgrupo", "P_liza", "Asegurado")),
    ("Riesgos1", "Subgrupo", ("id", "Tomador", "Subgrupo", "P_liza", "Asegurado")),
    ("Tasks", "Vendedor", ("id", "Vendedor", "ID_Tomador", "What_Id", "Who_Id", "N_mero_p_liza")),
)


def _safe_value(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        name = str(value.get("name") or "").strip()
        return {
            "kind": "lookup",
            "present": bool(value),
            "module": str(value.get("$se_module") or value.get("module") or ""),
            "id": fingerprint(value.get("id")),
            "target_name_match": name.casefold() == TARGET.casefold() if name else False,
        }
    clean = str(value or "").strip()
    return {
        "kind": "scalar",
        "present": bool(clean),
        "target_value_match": clean.casefold() == TARGET.casefold() if clean else False,
    }


@dataclass
class FonconstruimosDiscovery:
    zoho: object
    reads: dict[str, int] = field(default_factory=dict)

    def _count(self, operation: str) -> None:
        self.reads[operation] = self.reads.get(operation, 0) + 1

    def discover(self) -> dict[str, object]:
        self._count("organization.get")
        organization = self.zoho.organization.get()
        if self.zoho.profile != "sandbox" or organization.environment != "sandbox":
            raise ValueError("Zoho no confirmó el entorno Sandbox solicitado.")

        accounts = self._accounts_diagnostic()
        searches = tuple(
            self._search(module, api_name, selected)
            for module, api_name, selected in SEARCHES
        )
        return {
            "profile": "sandbox",
            "environment": "sandbox",
            "mode": "read_only",
            "target": TARGET,
            "accounts": accounts,
            "searches": searches,
            "reads": dict(sorted(self.reads.items())),
            "read_total": sum(self.reads.values()),
            "writes": 0,
            "raw_records_persisted": 0,
        }

    def _accounts_diagnostic(self) -> dict[str, object]:
        result: dict[str, object] = {}
        self._count("metadata.Accounts")
        try:
            fields = self.zoho.metadata.list_fields("Accounts")
            names = {item.api_name for item in fields}
            result["metadata"] = "ok"
            result["account_name_available"] = "Account_Name" in names
        except ZohoError as exc:
            result["metadata"] = "error"
            result["metadata_category"] = exc.category

        self._count("records.Accounts")
        try:
            page = self.zoho.records.list(module="Accounts", fields=("id",), page=1, limit=1)
            result["records"] = "ok"
            result["sample_count"] = min(len(page.records), 1)
        except ZohoError as exc:
            result["records"] = "error"
            result["records_category"] = exc.category
        return result

    def _search(self, module: str, api_name: str, selected: tuple[str, ...]) -> dict[str, object]:
        self._count(f"search.{module}.{api_name}")
        try:
            page = self.zoho.search.by_field(
                module=module,
                field=api_name,
                value=TARGET,
                fields=selected,
                page=1,
                limit=LIMIT,
            )
        except ZohoError as exc:
            return {"module": module, "field": api_name, "status": "error", "category": exc.category}
        records = tuple(page.records[:LIMIT])
        return {
            "module": module,
            "field": api_name,
            "status": "ok",
            "matches": len(records),
            "records": tuple({
                "id": fingerprint(record.get("id")),
                "fields": {
                    field_name: _safe_value(record.get(field_name))
                    for field_name in selected
                    if field_name != "id"
                },
            } for record in records),
        }


@dataclass
class FonconstruimosInsuredResolution:
    """Resuelve sólo la relación confirmada Riesgos1.Asegurado → Contacts."""

    zoho: object
    reads: dict[str, int] = field(default_factory=dict)

    def _count(self, operation: str) -> None:
        self.reads[operation] = self.reads.get(operation, 0) + 1

    def discover(self) -> dict[str, object]:
        self._count("organization.get")
        organization = self.zoho.organization.get()
        if self.zoho.profile != "sandbox" or organization.environment != "sandbox":
            raise ValueError("Zoho no confirmó el entorno Sandbox solicitado.")
        self._count("search.Riesgos1.Tomador")
        page = self.zoho.search.by_field(
            module="Riesgos1",
            field="Tomador",
            value=TARGET,
            fields=("id", "Asegurado"),
            page=1,
            limit=LIMIT,
        )
        contact_ids = tuple(dict.fromkeys(
            str(lookup.get("id") or "")
            for record in page.records[:LIMIT]
            if isinstance((lookup := record.get("Asegurado")), dict) and lookup.get("id")
        ))
        contacts = []
        failures = []
        for contact_id in contact_ids:
            self._count("records.Contacts.get_by_id")
            try:
                record = self.zoho.records.get_by_id(
                    module="Contacts",
                    record_id=contact_id,
                    fields=("id", "Empresa", "Grupo_econ_mico", "Tipo_de_persona"),
                )
            except ZohoError as exc:
                failures.append(exc.category)
                continue
            contacts.append({
                "id": fingerprint(record.get("id")),
                "empresa": _safe_value(record.get("Empresa")),
                "grupo_economico": _safe_value(record.get("Grupo_econ_mico")),
                "person_type_present": bool(str(record.get("Tipo_de_persona") or "").strip()),
            })
        return {
            "profile": "sandbox",
            "mode": "read_only",
            "confirmed_relation": "Riesgos1.Asegurado->Contacts",
            "risks1_matches": min(len(page.records), LIMIT),
            "contact_lookups": len(contact_ids),
            "contacts": tuple(contacts),
            "failures": tuple(failures),
            "reads": dict(sorted(self.reads.items())),
            "read_total": sum(self.reads.values()),
            "writes": 0,
            "raw_records_persisted": 0,
        }
