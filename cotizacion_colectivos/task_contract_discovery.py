"""Exploración dirigida y sanitizada del contrato de Tasks en Sandbox.

No contiene operaciones de escritura ni persiste respuestas de Zoho.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from integrations.zoho.exceptions import ZohoError


TASK_KINDS = ("Ingresos", "Retiros", "Cotización")
TASK_FIELDS = (
    "id",
    "Subject",
    "tipo_de_solicitud",
    "What_Id",
    "Who_Id",
    "Owner",
    "Responsable",
    "Status",
    "rea",
    "Due_Date",
    "Fecha_de_solicitud_del_cliente",
    "Fecha_y_hora_vencimiento",
    "ID_Tomador",
    "ID_asegurado",
    "ID_Riesgos1_task",
    "N_mero_p_liza",
    "Correo_del_solicitante",
)
COMPANY_FIELDS = ("id", "Full_Name", "Nombre_comercial", "Raz_n_social", "Empresa")
COMPANY_SEARCHES = (
    ("Contacts", "Nombre_comercial", COMPANY_FIELDS),
    ("Contacts", "Raz_n_social", COMPANY_FIELDS),
    ("Contacts", "Full_Name", COMPANY_FIELDS),
    ("Accounts", "Account_Name", ("id", "Account_Name")),
)


def fingerprint(value: object) -> str:
    clean = str(value or "").strip()
    if not clean:
        return ""
    digest = hashlib.sha256(f"colectivos-task-contract:{clean}".encode()).hexdigest()
    return f"sha256:{digest[:12]}"


def _presence(value: object) -> str:
    return "presente" if value not in (None, "", [], {}) else "vacío"


def _safe_category(value: object) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    return "[estructura]"


def _lookup(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {"present": _presence(value), "module": "", "id": ""}
    return {
        "present": _presence(value),
        "module": str(value.get("$se_module") or value.get("module") or ""),
        "id": fingerprint(value.get("id")),
    }


@dataclass
class DirectedTaskDiscovery:
    zoho: object
    reads: dict[str, int] = field(default_factory=dict)

    def _count(self, operation: str) -> None:
        self.reads[operation] = self.reads.get(operation, 0) + 1

    def discover(self) -> dict[str, object]:
        self._count("organization.get")
        organization = self.zoho.organization.get()
        if self.zoho.profile != "sandbox" or organization.environment != "sandbox":
            raise ValueError("Zoho no confirmó el entorno Sandbox solicitado.")

        company = self._find_company("Fonconstruimos")
        tasks = tuple(self._find_task(kind) for kind in TASK_KINDS)
        return {
            "profile": "sandbox",
            "environment": "sandbox",
            "mode": "read_only",
            "target": "Fonconstruimos",
            "company": company,
            "tasks": tasks,
            "reads": dict(sorted(self.reads.items())),
            "read_total": sum(self.reads.values()),
            "writes": 0,
            "raw_records_persisted": 0,
        }

    def _find_company(self, target: str) -> dict[str, object]:
        matches: dict[tuple[str, str], dict[str, object]] = {}
        sources: list[str] = []
        attempted: list[str] = []
        failures: list[dict[str, str]] = []
        for module, api_name, selected_fields in COMPANY_SEARCHES:
            self._count(f"search.{module}")
            attempted.append(f"{module}.{api_name}")
            try:
                page = self.zoho.search.by_field(
                    module=module,
                    field=api_name,
                    value=target,
                    fields=selected_fields,
                    page=1,
                    limit=1,
                )
            except ZohoError as exc:
                failures.append({"search": f"{module}.{api_name}", "category": exc.category})
                continue
            for record in page.records[:1]:
                record_id = str(record.get("id") or "")
                if record_id:
                    matches.setdefault((module, record_id), record)
                    sources.append(f"{module}.{api_name}")
        if not matches:
            return {
                "found": False,
                "searched": tuple(attempted),
                "failures": tuple(failures),
            }
        (module, record_id), record = next(iter(matches.items()))
        return {
            "found": True,
            "module": module,
            "record_id": fingerprint(record_id),
            "matched_by": tuple(dict.fromkeys(sources)),
            "failures": tuple(failures),
            "empresa": _lookup(record.get("Empresa")) if module == "Contacts" else {
                "present": "no aplica: el registro ya pertenece a Accounts",
                "module": "Accounts",
                "id": fingerprint(record_id),
            },
        }

    def _find_task(self, kind: str) -> dict[str, object]:
        self._count("search.Tasks")
        try:
            page = self.zoho.search.by_field(
                module="Tasks",
                field="tipo_de_solicitud",
                value=kind,
                fields=TASK_FIELDS,
                page=1,
                limit=1,
            )
        except ZohoError as exc:
            return {"kind": kind, "found": False, "failure": exc.category}
        if not page.records:
            return {"kind": kind, "found": False}
        record = page.records[0]
        return {
            "kind": kind,
            "found": True,
            "record_id": fingerprint(record.get("id")),
            "subject": _presence(record.get("Subject")),
            "what_id": _lookup(record.get("What_Id")),
            "who_id": _lookup(record.get("Who_Id")),
            "owner": _lookup(record.get("Owner")),
            "responsable": _presence(record.get("Responsable")),
            "status": _safe_category(record.get("Status")),
            "area": _safe_category(record.get("rea")),
            "due_date": _safe_category(record.get("Due_Date")),
            "request_date": _safe_category(record.get("Fecha_de_solicitud_del_cliente")),
            "due_datetime": _safe_category(record.get("Fecha_y_hora_vencimiento")),
            "requester_email": _presence(record.get("Correo_del_solicitante")),
            "id_tomador": fingerprint(record.get("ID_Tomador")),
            "id_asegurado": fingerprint(record.get("ID_asegurado")),
            "id_riesgos1": fingerprint(record.get("ID_Riesgos1_task")),
            "policy_number": _presence(record.get("N_mero_p_liza")),
        }
