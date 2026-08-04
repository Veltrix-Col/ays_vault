from __future__ import annotations

import hashlib
import io
import json
import zipfile
from dataclasses import dataclass

from django.conf import settings
from django.core import signing
from django.core.exceptions import ValidationError
from django.utils import timezone
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill

from vault.crypto import decrypt

from ..models import CambioSolicitudColectivo, RespuestaSolicitudColectivo, SolicitudColectivo
from .external import response_checksum

TEMPLATE_VERSION = 1
MAX_ROWS = 5000
HEADERS = ("Acción", "Tipo ID asociado", "ID asociado", "Nombre asociado", "Tipo ID asegurado", "ID asegurado", "Nombre asegurado", "Póliza", "Ramo", "Código de ramo", "Aseguradora", "Estado actual", "Estado solicitado", "Fecha de ingreso", "Fecha de retiro", "Fecha efectiva", "Parentesco", "Plan actual", "Plan solicitado", "Pago mensual con IVA actual", "Pago asegurado sin IVA actual", "Valor anterior", "Valor nuevo", "Tipo de novedad", "Motivo", "Observaciones", "Requiere adjunto", "Referencia de fila")


def _safe(value):
    if value is None:
        return ""
    text = str(value)
    return "'" + text if text.startswith(("=", "+", "-", "@")) else text


def _metadata_signature(request: SolicitudColectivo, nonce: str) -> str:
    return signing.dumps({"request": str(request.uuid), "branch": request.branch_code, "snapshot": request.snapshot_revision, "nonce": nonce}, salt="colectivos.excel.v1", compress=True)


def build_novelties_template(request: SolicitudColectivo) -> bytes:
    nonce = hashlib.sha256(f"{request.uuid}:{timezone.now().isoformat()}".encode()).hexdigest()[:24]
    book = Workbook()
    sheet = book.active
    sheet.title = "Novedades"
    sheet.append(HEADERS)
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="155A96")
    for record in request.records.order_by("original_position")[:MAX_ROWS]:
        sheet.append(("SIN_CAMBIOS", "", "", "", "", "", "", request.masked_policy_reference, request.branch_name, request.branch_code, "", record.initial_status, "", record.entry_date, record.exit_date, "", "", record.plan, "", "", "", "", "", "", "", "", "No", str(record.public_key)))
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    policy = book.create_sheet("Póliza")
    for row in (("Solicitud", request.public_id), ("Póliza", request.masked_policy_reference), ("Ramo", request.branch_name), ("Fecha límite", request.deadline)):
        policy.append(row)
    instructions = book.create_sheet("Instrucciones")
    instructions.append(("Use únicamente las acciones del catálogo. No agregue fórmulas, macros ni cambie la hoja Metadatos.",))
    catalogs = book.create_sheet("Catálogos")
    catalogs.append(("Acciones", "Tipos de novedad", "Tipos de identificación"))
    for index, action in enumerate(CambioSolicitudColectivo.Action.values, 2):
        catalogs.cell(index, 1, action)
    metadata = book.create_sheet("Metadatos")
    metadata.sheet_state = "hidden"
    values = (("version", TEMPLATE_VERSION), ("request", request.public_id), ("branch", request.branch_code), ("snapshot", request.snapshot_revision), ("nonce", nonce), ("checksum", _metadata_signature(request, nonce)))
    for row in values:
        metadata.append(row)
    stream = io.BytesIO()
    book.save(stream)
    return stream.getvalue()


@dataclass(frozen=True)
class ExcelPreview:
    rows: tuple[dict[str, str], ...]
    counts: dict[str, int]
    warnings: tuple[str, ...]


def parse_novelties(uploaded, request: SolicitudColectivo) -> ExcelPreview:
    name = str(getattr(uploaded, "name", "")).casefold()
    if not name.endswith(".xlsx") or name.endswith(".xlsm") or getattr(uploaded, "size", 0) > settings.COLECTIVOS_ATTACHMENT_MAX_BYTES:
        raise ValidationError("El archivo XLSX no es válido.")
    raw = uploaded.read()
    uploaded.seek(0)
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            infos = archive.infolist()
            if len(infos) > 2_000 or sum(item.file_size for item in infos) > 50 * 1024 * 1024:
                raise ValidationError("El archivo XLSX excede los límites de seguridad.")
            names = {item.filename.casefold() for item in infos}
            if any("vbaproject" in item or item.endswith(".bin") or "externallinks" in item for item in names):
                raise ValidationError("El archivo contiene contenido no permitido.")
            for info in infos:
                if info.filename.casefold().endswith(".rels"):
                    relationship_xml = archive.read(info)
                    if b'TargetMode="External"' in relationship_xml or b"TargetMode='External'" in relationship_xml:
                        raise ValidationError("El archivo contiene vínculos externos no permitidos.")
        book = load_workbook(io.BytesIO(raw), read_only=True, data_only=False, keep_links=False)
    except (zipfile.BadZipFile, OSError, ValueError) as exc:
        raise ValidationError("El archivo XLSX no es válido.") from exc
    required = {"Novedades", "Póliza", "Instrucciones", "Catálogos", "Metadatos"}
    if not required.issubset(book.sheetnames):
        raise ValidationError("Faltan hojas obligatorias.")
    meta = {str(row[0].value): row[1].value for row in book["Metadatos"].iter_rows(min_row=1, max_col=2) if row[0].value}
    try:
        payload = signing.loads(str(meta.get("checksum", "")), salt="colectivos.excel.v1", max_age=60 * 60 * 24 * 30)
    except signing.BadSignature as exc:
        raise ValidationError("Los metadatos no superan la validación.") from exc
    if payload.get("request") != str(request.uuid) or payload.get("branch") != request.branch_code or payload.get("snapshot") != request.snapshot_revision or meta.get("version") != TEMPLATE_VERSION:
        raise ValidationError("La plantilla no corresponde a esta solicitud.")
    sheet = book["Novedades"]
    headers = tuple(cell.value for cell in next(sheet.iter_rows(min_row=1, max_row=1)))
    if headers != HEADERS:
        raise ValidationError("Los encabezados fueron alterados.")
    rows, seen, counts = [], set(), {action: 0 for action in CambioSolicitudColectivo.Action.values}
    zero_payments = negative_payments = 0
    for position, cells in enumerate(sheet.iter_rows(min_row=2), 1):
        if position > MAX_ROWS:
            raise ValidationError("El archivo supera el máximo de filas.")
        if any(cell.data_type == "f" or getattr(cell, "hyperlink", None) for cell in cells):
            raise ValidationError("No se permiten fórmulas ni hipervínculos.")
        values = {HEADERS[index]: _safe(cell.value).strip() for index, cell in enumerate(cells[:len(HEADERS)])}
        if not any(values.values()):
            continue
        action = values["Acción"].upper()
        if action not in counts:
            raise ValidationError("Existe una acción no permitida.")
        reference = values["Referencia de fila"]
        if action != "INCLUIR":
            record = request.records.filter(public_key=reference).first()
            if not record or reference in seen:
                raise ValidationError("Existe una referencia duplicada o inválida.")
            seen.add(reference)
        rows.append({"record": reference, "action": action, "plan": values["Plan solicitado"], "parentesco": values["Parentesco"], "fecha_efectiva": values["Fecha efectiva"], "fecha_ingreso": values["Fecha de ingreso"], "fecha_retiro": values["Fecha de retiro"], "motivo": values["Motivo"], "observaciones": values["Observaciones"]})
        counts[action] += 1
        for heading in ("Pago mensual con IVA actual", "Pago asegurado sin IVA actual"):
            raw_payment = values[heading].replace(",", "").strip()
            if not raw_payment:
                continue
            try:
                number = float(raw_payment)
            except ValueError:
                continue
            zero_payments += number == 0
            negative_payments += number < 0
    counts.update({"VALIDAS": len(rows), "ERRORES": 0, "ADVERTENCIAS": 0, "DUPLICADOS": 0, "PAGOS_CERO": zero_payments, "PAGOS_NEGATIVOS": negative_payments})
    return ExcelPreview(tuple(rows), counts, tuple())


def build_comparison(response: RespuestaSolicitudColectivo) -> bytes:
    book = Workbook()
    summary = book.active
    summary.title = "Resumen"
    for row in (("Solicitud", response.request.public_id), ("Ramo", response.request.branch_name), ("Versión", response.version), ("Estado", response.status)):
        summary.append(row)
    changes = book.create_sheet("Cambios")
    changes.append(("Registro", "Campo", "Valor inicial", "Valor solicitado", "Decisión", "Valor aprobado", "Observación"))
    for change in response.changes.select_related("original_record"):
        review = change.reviews.order_by("-version").first()
        changes.append((str(change.original_record.public_key) if change.original_record else "INCLUSIÓN", change.functional_field, decrypt(change.encrypted_previous_value), decrypt(change.encrypted_new_value), review.decision if review else "PENDIENTE", decrypt(review.encrypted_approved_value) if review else "", ""))
    book.create_sheet("Novedades")
    book.create_sheet("Advertencias")
    metadata = book.create_sheet("Metadatos")
    metadata.sheet_state = "hidden"
    metadata.append(("checksum", response.checksum))
    stream = io.BytesIO(); book.save(stream); return stream.getvalue()


def build_response_workbook(response: RespuestaSolicitudColectivo) -> bytes:
    book = Workbook()
    summary = book.active
    summary.title = "Resumen"
    for row in (
        ("Solicitud", response.request.public_id),
        ("Ramo", response.request.branch_name),
        ("Origen", response.origin),
        ("Versión", response.version),
        ("Estado", response.status),
        ("Fecha de envío", response.submitted_at or ""),
    ):
        summary.append(tuple(_safe(value) for value in row))
    changes = book.create_sheet("Respuesta")
    changes.append(("Referencia", "Acción", "Campo", "Valor inicial", "Valor solicitado"))
    for change in response.changes.select_related("original_record"):
        changes.append((
            str(change.original_record.public_key) if change.original_record else "INCLUSIÓN",
            change.action,
            change.functional_field,
            _safe(decrypt(change.encrypted_previous_value)),
            _safe(decrypt(change.encrypted_new_value)),
        ))
    metadata = book.create_sheet("Metadatos")
    metadata.sheet_state = "hidden"
    metadata.append(("checksum", response.checksum))
    stream = io.BytesIO()
    book.save(stream)
    return stream.getvalue()


def build_approved_consolidated(response: RespuestaSolicitudColectivo) -> bytes:
    if response.status != response.Status.APPROVED or response.request.status != response.request.Status.APPROVED:
        raise ValidationError("La respuesta todavía no está aprobada.")
    changes = list(response.changes.select_related("original_record").prefetch_related("reviews"))
    latest_reviews = [(change, change.reviews.order_by("-version").first()) for change in changes]
    if not latest_reviews or any(review is None for _, review in latest_reviews):
        raise ValidationError("Existen decisiones pendientes.")
    if any(review.decision not in {"APROBAR", "APROBAR_CON_AJUSTE"} for _, review in latest_reviews):
        raise ValidationError("Existen decisiones no aprobadas.")
    book = Workbook()
    sheet = book.active
    sheet.title = "Consolidado aprobado"
    sheet.append((
        "Referencia", "Póliza", "Ramo", "Tipo de novedad", "Campo",
        "Valor anterior", "Valor aprobado", "Estado de revisión",
        "Responsable", "Solicitud",
    ))
    for change, review in latest_reviews:
        approved = decrypt(review.encrypted_approved_value) or decrypt(change.encrypted_new_value)
        sheet.append((
            str(change.original_record.public_key) if change.original_record else "INCLUSIÓN",
            response.request.masked_policy_reference,
            response.request.branch_name,
            change.action,
            change.functional_field,
            _safe(decrypt(change.encrypted_previous_value)),
            _safe(approved),
            review.decision,
            review.reviewer.get_username(),
            response.request.public_id,
        ))
    metadata = book.create_sheet("Metadatos")
    metadata.sheet_state = "hidden"
    metadata.append(("checksum", response.checksum))
    stream = io.BytesIO()
    book.save(stream)
    return stream.getvalue()
