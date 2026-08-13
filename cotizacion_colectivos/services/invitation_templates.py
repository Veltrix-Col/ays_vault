from __future__ import annotations

import hashlib
import io
import logging
import math
import re
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

from django.conf import settings

from ..invitation_templates.catalog import InvitationTemplate, templates_for_branch
from ..zoho import get_colectivos_profile
from .common import ColectivosServiceError, unsign_record_context
from .preparations import load_policy_preparation


logger = logging.getLogger("cotizacion_colectivos")
NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_DOC_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
NS_CUSTOM = "http://schemas.openxmlformats.org/officeDocument/2006/custom-properties"
ET.register_namespace("", NS_MAIN)
SAFE_NAME = re.compile(r"[^A-Za-z0-9_-]+")


@dataclass(frozen=True)
class TemplatePreview:
    template: InvitationTemplate
    status: str
    automatic_fields: int
    manual_fields: int
    rows: int
    capacity: int
    missing_required: tuple[str, ...]
    message: str = ""
    output_files: int = 1


@dataclass(frozen=True)
class GeneratedTemplate:
    template: InvitationTemplate
    filename: str
    content: bytes


def _local_workspace(token: str):
    context = unsign_record_context(token, "policy")
    profile = get_colectivos_profile()
    backend = str(getattr(settings, "ZOHO_BACKEND", "sdk")).strip().lower()
    loaded = load_policy_preparation(
        token=token, profile=profile, backend=backend,
        source_kind=context.get("source_kind"),
    )
    if loaded is None:
        raise ColectivosServiceError(
            "workspace_unavailable",
            "Actualice la información de la póliza antes de preparar las plantillas.",
        )
    return (*loaded, context, profile, backend)


def _attributes(member) -> dict[str, str]:
    return {str(key): str(value or "").strip() for key, value in member.risk_attributes}


def _vehicle_rows(members) -> tuple[dict[str, str], ...]:
    rows = []
    seen = set()
    for member in members:
        attributes = _attributes(member)
        identity = member.risk_key or "|".join((
            attributes.get("placa", ""), attributes.get("modelo", ""),
            member.insured_key, member.document,
        ))
        if not identity or identity in seen:
            continue
        if not any(attributes.get(key) for key in ("placa", "modelo", "marca", "vehiculo")):
            continue
        seen.add(identity)
        rows.append({
            "vehicle.plate": attributes.get("placa", ""),
            "vehicle.model": attributes.get("modelo", ""),
            "vehicle.brand": attributes.get("marca", ""),
            "vehicle.city": attributes.get("ciudad", ""),
            "vehicle.use": attributes.get("tipo_uso", ""),
            "insured.id_type": member.insured_id_type or member.id_type,
            "insured.document": member.insured_document or member.document,
            "insured.name": member.insured_name or member.display_name,
            "insured.relationship": member.relationship,
        })
    return tuple(rows)


def _context(detail, members, token_context) -> tuple[dict[str, str], tuple[dict[str, str], ...]]:
    # The holder identifier must come from the exact source Contact captured
    # in the encrypted Workspace. Never infer it from another insured/affiliate.
    source_document = str(
        getattr(detail.source_summary, "document", "") if detail.source_summary else ""
    ).strip()
    fixed = {
        "policy.holder": detail.holder,
        "holder.document": source_document,
        "holder.city": detail.source_summary.city if detail.source_summary else "",
        "policy.start_date": detail.start_date,
        "policy.end_date": detail.end_date,
        "policy.current_insurer": detail.insurer,
        "policy.payment_mode": detail.payment_mode,
    }
    return fixed, _vehicle_rows(members)


def preview_invitation_templates(token: str):
    started = time.monotonic()
    detail, members, metadata, context, profile, backend = _local_workspace(token)
    fixed, rows = _context(detail, members, context)
    previews = []
    for template in templates_for_branch(detail.branch_code):
        capacity = template.end_row - template.start_row + 1
        template_rows = rows if any("{row}" in field.position for field in template.fields) else ()
        if not template.active:
            previews.append(TemplatePreview(
                template, "unavailable",
                sum(field.automatic for field in template.fields),
                sum(not field.automatic for field in template.fields),
                len(template_rows), capacity,
                (), template.limitation,
            ))
            continue
        missing = []
        for field in template.fields:
            if not field.required or not field.automatic:
                continue
            values = template_rows if "{row}" in field.position else (fixed,)
            if not values or any(not row.get(field.source) for row in values):
                missing.append(field.destination)
        output_files = 1
        status = "ready"
        message = "Lista para descargar."
        if len(template_rows) > capacity:
            if template.supports_chunking:
                output_files = math.ceil(len(template_rows) / capacity)
                if missing or any(not field.automatic for field in template.fields):
                    status = "ready_manual"
                message = (
                    f"Lista: se generarán {output_files} archivos sin truncar "
                    f"los {len(template_rows)} registros; los datos no disponibles quedan vacíos."
                )
            else:
                status = "validation"
                message = "El formato requiere ajuste manual por capacidad; no se entregarán datos parciales."
        elif missing or any(not field.automatic for field in template.fields):
            status = "ready_manual"
            message = "Lista para descargar; los datos no disponibles quedan vacíos para completar."
        previews.append(TemplatePreview(
            template, status,
            sum(field.automatic for field in template.fields),
            sum(not field.automatic for field in template.fields),
            len(template_rows), capacity, tuple(dict.fromkeys(missing)), message,
            output_files,
        ))
    logger.info(
        "colectivos_invitation_templates application=cotizacion_colectivos operation=preview "
        "profile=%s backend=%s cache=hit templates=%d records=%d total_ms=%d",
        profile, backend, len(previews), len(rows), round((time.monotonic() - started) * 1000),
    )
    return detail, tuple(previews), metadata


def _sheet_part(source: zipfile.ZipFile, sheet_name: str) -> str:
    workbook = ET.fromstring(source.read("xl/workbook.xml"))
    rel_id = None
    for sheet in workbook.findall(f"{{{NS_MAIN}}}sheets/{{{NS_MAIN}}}sheet"):
        if sheet.get("name") == sheet_name:
            rel_id = sheet.get(f"{{{NS_DOC_REL}}}id")
            break
    if not rel_id:
        raise ValueError("sheet")
    rels = ET.fromstring(source.read("xl/_rels/workbook.xml.rels"))
    for rel in rels.findall(f"{{{NS_PKG_REL}}}Relationship"):
        if rel.get("Id") == rel_id:
            target = rel.get("Target", "").lstrip("/")
            return target if target.startswith("xl/") else f"xl/{target}"
    raise ValueError("relationship")


def _set_cell(root: ET.Element, coordinate: str, value: str) -> None:
    cell = root.find(f".//{{{NS_MAIN}}}c[@r='{coordinate}']")
    if cell is None:
        raise ValueError(f"cell:{coordinate}")
    formula = cell.find(f"{{{NS_MAIN}}}f")
    if formula is not None:
        raise ValueError(f"formula:{coordinate}")
    for child in list(cell):
        if child.tag in {f"{{{NS_MAIN}}}v", f"{{{NS_MAIN}}}is"}:
            cell.remove(child)
    cell.set("t", "inlineStr")
    inline = ET.SubElement(cell, f"{{{NS_MAIN}}}is")
    text = ET.SubElement(inline, f"{{{NS_MAIN}}}t")
    text.text = str(value or "")


def _column_number(value: str) -> int:
    result = 0
    for character in value:
        result = result * 26 + ord(character) - 64
    return result


def _column_name(value: int) -> str:
    result = ""
    while value:
        value, remainder = divmod(value - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _coordinates(position: str) -> tuple[str, ...]:
    match = re.fullmatch(r"([A-Z]+)(\d+)(?::([A-Z]+)(\d+))?", position)
    if not match:
        raise ValueError(f"position:{position}")
    start_column, start_row, end_column, end_row = match.groups()
    if end_column is None:
        return (position,)
    if start_row != end_row:
        raise ValueError(f"range:{position}")
    return tuple(
        f"{_column_name(column)}{start_row}"
        for column in range(_column_number(start_column), _column_number(end_column) + 1)
    )


def _patch_xlsx(template: InvitationTemplate, fixed, rows) -> bytes:
    original = template.path.read_bytes()
    source_buffer = io.BytesIO(original)
    output = io.BytesIO()
    changes: dict[str, dict[str, str]] = {}
    for coordinate in template.clear_cells:
        changes.setdefault(template.data_sheet, {})[coordinate] = ""
    # A generated chunk must never inherit rows from a previous quote stored
    # in the master. Clear every mapped row, including fields that are manual,
    # then write only the current chunk's confirmed automatic values.
    for field in template.fields:
        if "{row}" not in field.position:
            continue
        for row_number in range(template.start_row, template.end_row + 1):
            for coordinate in _coordinates(field.position.format(row=row_number)):
                changes.setdefault(field.sheet, {})[coordinate] = ""
    for field in template.fields:
        if not field.automatic:
            continue
        if "{row}" in field.position:
            for offset, row_data in enumerate(rows):
                coordinates = _coordinates(
                    field.position.format(row=template.start_row + offset)
                )
                value = row_data.get(field.source, "")
                if len(coordinates) == 1:
                    changes.setdefault(field.sheet, {})[coordinates[0]] = value
        else:
            value = fixed.get(field.source, "")
            if field.source == "policy.payment_mode" and value not in {"Contado", "Financiado", "Mensual", "Anual"}:
                value = ""
            changes.setdefault(field.sheet, {})[field.position] = value
    with zipfile.ZipFile(source_buffer, "r") as source, zipfile.ZipFile(output, "w") as target:
        replacements = {}
        for sheet_name, values in changes.items():
            part = _sheet_part(source, sheet_name)
            root = ET.fromstring(source.read(part))
            for coordinate, value in values.items():
                _set_cell(root, coordinate, value)
            replacements[part] = ET.tostring(root, encoding="utf-8", xml_declaration=True)
        # Some official masters retain hidden properties from a previous quote.
        # Keep the package relationship valid but remove every custom value from
        # the distributable copy. The source file remains byte-for-byte intact.
        if "docProps/custom.xml" in source.namelist():
            clean_properties = ET.Element(f"{{{NS_CUSTOM}}}Properties")
            replacements["docProps/custom.xml"] = ET.tostring(
                clean_properties, encoding="utf-8", xml_declaration=True,
            )
        for info in source.infolist():
            target.writestr(info, replacements.get(info.filename, source.read(info.filename)))
    if hashlib.sha256(template.path.read_bytes()).digest() != hashlib.sha256(original).digest():
        raise RuntimeError("La plantilla maestra cambió durante la generación.")
    return output.getvalue()


def generate_invitation_templates(token: str):
    started = time.monotonic()
    detail, members, _metadata, context, profile, backend = _local_workspace(token)
    fixed, rows = _context(detail, members, context)
    generated, errors = [], []
    for template in templates_for_branch(detail.branch_code, active_only=True):
        template_rows = rows if any("{row}" in field.position for field in template.fields) else ()
        capacity = template.end_row - template.start_row + 1
        if len(template_rows) > capacity and not template.supports_chunking:
            errors.append((template.insurer_name, "capacidad"))
            continue
        chunks = (
            tuple(
                template_rows[offset : offset + capacity]
                for offset in range(0, len(template_rows), capacity)
            )
            if template_rows else ((),)
        )
        for position, chunk in enumerate(chunks, start=1):
            try:
                content = _patch_xlsx(template, fixed, chunk)
            except (OSError, ValueError, KeyError, zipfile.BadZipFile, RuntimeError):
                logger.exception(
                    "colectivos_invitation_templates application=cotizacion_colectivos operation=generate "
                    "profile=%s backend=%s template=%s category=template_error",
                    profile, backend, template.code,
                )
                errors.append((template.insurer_name, "estructura"))
                break
            branch = "movilidad" if detail.branch_code == "40" else detail.branch_code
            safe = SAFE_NAME.sub(
                "_", f"{template.insurer_code.casefold()}_{branch}"
            ).strip("_")
            suffix = f"_{position:02d}" if len(chunks) > 1 else ""
            generated.append(GeneratedTemplate(template, f"{safe}{suffix}.xlsx", content))
    logger.info(
        "colectivos_invitation_templates application=cotizacion_colectivos operation=generate "
        "profile=%s backend=%s cache=hit templates=%d errors=%d records=%d total_ms=%d",
        profile, backend, len(generated), len(errors), len(rows),
        round((time.monotonic() - started) * 1000),
    )
    if not generated:
        raise ColectivosServiceError("template_unavailable", "No hay plantillas generables para este ramo.")
    if len(generated) == 1:
        item = generated[0]
        return item.content, item.filename, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", tuple(errors)
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for item in generated:
            bundle.writestr(item.filename, item.content)
    return archive.getvalue(), f"Invitaciones_{detail.branch_code}.zip", "application/zip", tuple(errors)
