from __future__ import annotations

from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

from .services.common import unsign_record_id
from .services.policies import CONTACT_BATCH_FIELDS, PolicyService

MAX_EXPORT_ROWS = 5000


def _safe_cell(value: object) -> object:
    if value is None:
        return ""
    if isinstance(value, (int, float, bool)):
        return value
    text = str(value)
    if text.startswith(("=", "+", "-", "@")):
        return "'" + text
    return text


def build_current_policy_workbook(token: str, service: PolicyService | None = None) -> bytes:
    service = service or PolicyService()
    detail = service.detail(token)
    policy_id = unsign_record_id(token, "policy")
    relations, truncated = service._relations(policy_id)
    relations = relations[:MAX_EXPORT_ROWS]
    contact_ids = {
        lookup_id for item in relations
        for field in ("Asegurado", "Afiliado", "Beneficiario")
        if (lookup_id := _lookup_id(item.get(field)))
    }
    contacts = service._batch("Contacts", CONTACT_BATCH_FIELDS, contact_ids)

    workbook = Workbook()
    current = workbook.active
    current.title = "Información actual"
    headers = (
        "Tipo ID asociado", "ID asociado", "Nombre asociado", "Tipo ID asegurado",
        "ID asegurado", "Nombre asegurado", "Póliza", "Ramo", "Código de ramo",
        "Aseguradora", "Estado asegurado", "Fecha de ingreso", "Fecha de retiro",
        "Parentesco", "Plan", "Prima", "Pago total", "Pago según forma de pago",
        "Pago empleado sin IVA", "Valor asegurado", "Observaciones actuales",
    )
    current.append(headers)
    for item in relations:
        insured_id = _lookup_id(item.get("Asegurado"))
        affiliate_id = _lookup_id(item.get("Afiliado"))
        insured = contacts.get(insured_id, {})
        associate = contacts.get(affiliate_id, insured)
        current.append(tuple(_safe_cell(value) for value in (
            associate.get("Tipo_ID"), associate.get("N_mero_de_ID"), associate.get("Full_Name"),
            insured.get("Tipo_ID"), insured.get("N_mero_de_ID"), insured.get("Full_Name"),
            detail.masked_reference, detail.branch_name, detail.branch_code, detail.insurer,
            item.get("Estado"), item.get("Fecha_ingreso_riesgo"), item.get("Fecha_salida_riesgo"),
            item.get("Parentesco"), item.get("Plan"), item.get("Prima"), item.get("Pago_total"),
            item.get("Pago_total_Seg_n_la_forma_de_pago_Valor_asegura"), item.get("Pago_EMPLEADO_Sin_IVA"),
            item.get("Valor_asegurado"), item.get("Observaciones"),
        )))

    policy = workbook.create_sheet("Información de póliza")
    policy.append(("Campo", "Valor"))
    for label, value in (
        ("Póliza", detail.masked_reference), ("Ramo", detail.branch_name),
        ("Código", detail.branch_code), ("Aseguradora", detail.insurer),
        ("Inicio de vigencia", detail.start_date), ("Fin de vigencia", detail.end_date),
        ("Estado", detail.state), ("Modo de pago", detail.payment_mode),
        ("Frecuencia", detail.frequency), ("Número de cuotas", detail.installments),
        ("Primera cuota", detail.first_installment_date), ("Perfil", service.profile),
        ("Modo", "Solo lectura"), ("Resultado parcial", "Sí" if truncated else "No"),
    ):
        policy.append((_safe_cell(label), _safe_cell(value)))

    metadata = workbook.create_sheet("Metadatos")
    metadata.sheet_state = "hidden"
    metadata.append(("Versión de plantilla", "1"))
    metadata.append(("Ramo", detail.branch_name))
    metadata.append(("Código", detail.branch_code))
    metadata.append(("Perfil", service.profile))
    metadata.append(("Modo", "Solo lectura"))

    for sheet in workbook.worksheets:
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        for cell in sheet[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="0875B8")
        for column in sheet.columns:
            width = min(max(len(str(cell.value or "")) for cell in column) + 2, 36)
            sheet.column_dimensions[column[0].column_letter].width = width
        for row in sheet.iter_rows():
            for cell in row:
                if cell.column in {2, 5} and sheet.title == "Información actual":
                    cell.number_format = "@"
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def _lookup_id(value: object) -> str:
    return str(value.get("id") or "") if isinstance(value, dict) else ""
