from __future__ import annotations

import hashlib
import io
import logging
import math
import re
import time
import zipfile
from copy import deepcopy
from dataclasses import dataclass, replace
from pathlib import Path
from xml.etree import ElementTree as ET

from django.conf import settings
from django.core import signing

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
BRANCH_INVITATION_SALT = "cotizacion_colectivos.branch_invitations.v1"


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
    columns: tuple[str, ...] = ()
    preview_rows: tuple[tuple[str, ...], ...] = ()


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


def _vehicle_rows(members, detail=None) -> tuple[dict[str, str], ...]:
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
            "policy.full_reference": getattr(detail, "full_reference", ""),
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
    return fixed, _vehicle_rows(members, detail)


def _operational_rows(rows):
    """Expose only the already-confirmed local fields used by the internal UI."""
    return tuple({
        "document": row.get("insured.document", ""),
        "plate": row.get("vehicle.plate", ""),
        "model": row.get("vehicle.model", ""),
        "brand": row.get("vehicle.brand", ""),
        "city": row.get("vehicle.city", ""),
        "relationship": row.get("insured.relationship", ""),
        "insured_name": row.get("insured.name", ""),
    } for row in rows)


def sign_branch_invitation_context(
    *, policy_tokens, branch_code: str, holder: str, policy_references=(),
) -> str:
    tokens = tuple(dict.fromkeys(str(token) for token in policy_tokens if token))
    references = tuple(str(value or "").strip()[:80] for value in policy_references)
    if not tokens or len(tokens) > 20:
        raise ColectivosServiceError("invalid_record", "El contexto del ramo no es válido.")
    return signing.dumps(
        {
            "policies": tokens, "references": references,
            "branch": str(branch_code), "holder": str(holder)[:160],
        },
        salt=BRANCH_INVITATION_SALT, compress=True,
    )


def _branch_workspace(token: str, *, require_complete=False):
    try:
        payload = signing.loads(
            token, salt=BRANCH_INVITATION_SALT,
            max_age=getattr(settings, "COLECTIVOS_SIGNED_ID_MAX_AGE_SECONDS", 1800),
        )
    except signing.BadSignature as exc:
        raise ColectivosServiceError("invalid_record", "El contexto del ramo no es válido.") from exc
    tokens = tuple(payload.get("policies") or ())
    references = tuple(payload.get("references") or ())
    branch_code = str(payload.get("branch") or "")
    if not tokens or len(tokens) > 20 or not branch_code:
        raise ColectivosServiceError("invalid_record", "El contexto del ramo no es válido.")
    workspaces = []
    missing = []
    for index, policy_token in enumerate(tokens):
        try:
            workspaces.append(_local_workspace(policy_token))
        except ColectivosServiceError as exc:
            if exc.code != "workspace_unavailable":
                raise
            missing.append(
                references[index] if index < len(references) and references[index]
                else f"póliza {index + 1}"
            )
    if not workspaces:
        raise ColectivosServiceError(
            "workspace_unavailable",
            "No hay workspaces locales vigentes para las pólizas de este ramo. "
            "Abra cada póliza indicada y actualice su información antes de consolidar.",
        )
    if missing and require_complete:
        raise ColectivosServiceError(
            "workspace_unavailable",
            "Falta actualizar el workspace local de: " + ", ".join(missing) + ".",
        )
    workspaces = tuple(workspaces)
    if any(item[0].branch_code != branch_code for item in workspaces):
        raise ColectivosServiceError("invalid_record", "Las pólizas no pertenecen al mismo ramo.")
    details = tuple(item[0] for item in workspaces)
    contexts = tuple(item[3] for item in workspaces)
    source_identity = (
        contexts[0].get("source_kind"), contexts[0].get("source_id"),
    )
    if any(
        (context.get("source_kind"), context.get("source_id")) != source_identity
        for context in contexts
    ):
        raise ColectivosServiceError("invalid_record", "Las pólizas no pertenecen al mismo cliente.")
    fixed, rows = _context(details[0], workspaces[0][1], workspaces[0][3])
    combined_rows = []
    operational_groups = []
    for detail, members, _metadata, context, _profile, _backend in workspaces:
        _policy_fixed, policy_rows = _context(detail, members, context)
        combined_rows.extend(policy_rows)
        operational_groups.append({
            "policy_token": detail.detail_token,
            "policy_reference": detail.full_reference or detail.masked_reference,
            "insurer": detail.insurer,
            "rows": _operational_rows(policy_rows),
        })
    display = replace(
        details[0], full_reference=f"{len(details)} pólizas vigentes del ramo",
        masked_reference=f"{len(details)} pólizas",
    )
    metadata = {
        "storage": "database", "remote_queries": 0,
        "policy_count": len(details), "consolidated": True,
        "operational_groups": tuple(operational_groups),
        "missing_workspaces": tuple(missing),
        "complete": not missing,
    }
    return display, fixed, tuple(combined_rows), metadata, workspaces[0][4], workspaces[0][5]


def _invitation_context(token: str, *, consolidated=False, require_complete=False):
    if consolidated:
        return _branch_workspace(token, require_complete=require_complete)
    detail, members, metadata, context, profile, backend = _local_workspace(token)
    fixed, rows = _context(detail, members, context)
    metadata = {
        **metadata,
        "operational_groups": ({
            "policy_token": detail.detail_token,
            "policy_reference": detail.full_reference or detail.masked_reference,
            "insurer": detail.insurer,
            "rows": _operational_rows(rows),
        },),
        "missing_workspaces": (), "complete": True, "remote_queries": 0,
    }
    return detail, fixed, rows, metadata, profile, backend


def preview_invitation_templates(token: str, *, consolidated=False):
    started = time.monotonic()
    detail, fixed, rows, metadata, profile, backend = _invitation_context(
        token, consolidated=consolidated,
    )
    previews = []
    for template in templates_for_branch(detail.branch_code):
        declared_capacity = template.end_row - template.start_row + 1
        capacity = (
            max(declared_capacity, len(rows))
            if template.expandable_rows else declared_capacity
        )
        template_rows = rows if any("{row}" in field.position for field in template.fields) else ()
        row_fields = tuple(
            field for field in template.fields
            if field.automatic and "{row}" in field.position
        )
        columns = tuple(field.destination for field in row_fields)
        visible_rows = tuple(
            tuple(str(row.get(field.source, "")) for field in row_fields)
            for row in template_rows
        )
        if not template.active:
            previews.append(TemplatePreview(
                template, "unavailable",
                sum(field.automatic for field in template.fields),
                sum(not field.automatic for field in template.fields),
                len(template_rows), capacity,
                (), template.limitation, 1, columns, visible_rows,
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
            output_files, columns, visible_rows,
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


def _expand_template_rows(root: ET.Element, template: InvitationTemplate, last_row: int) -> None:
    if not template.expandable_rows or last_row <= template.end_row:
        return
    sheet_data = root.find(f"{{{NS_MAIN}}}sheetData")
    seed = root.find(f".//{{{NS_MAIN}}}row[@r='{template.end_row}']")
    if sheet_data is None or seed is None or seed.find(f".//{{{NS_MAIN}}}f") is not None:
        raise ValueError("expandable-row")
    for row_number in range(template.end_row + 1, last_row + 1):
        row = deepcopy(seed)
        row.set("r", str(row_number))
        for cell in row.findall(f"{{{NS_MAIN}}}c"):
            coordinate = str(cell.get("r") or "")
            cell.set("r", re.sub(r"\d+$", str(row_number), coordinate))
            for child in list(cell):
                if child.tag in {f"{{{NS_MAIN}}}v", f"{{{NS_MAIN}}}is"}:
                    cell.remove(child)
        sheet_data.append(row)
    dimension = root.find(f"{{{NS_MAIN}}}dimension")
    if dimension is not None:
        dimension.set("ref", re.sub(r"\d+$", str(last_row), dimension.get("ref", "")))


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
            if sheet_name == template.data_sheet and rows:
                _expand_template_rows(
                    root, template, template.start_row + len(rows) - 1,
                )
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


def generate_invitation_templates(
    token: str, *, template_code: str = "", insurer_code: str = "",
    consolidated=False,
):
    started = time.monotonic()
    detail, fixed, rows, _metadata, profile, backend = _invitation_context(
        token, consolidated=consolidated, require_complete=not consolidated,
    )
    generated, errors = [], []
    templates = templates_for_branch(detail.branch_code, active_only=True)
    if template_code:
        templates = tuple(item for item in templates if item.code == template_code)
        if not templates:
            raise ColectivosServiceError("template_unavailable", "La plantilla solicitada no está disponible para este ramo.")
    if insurer_code:
        templates = tuple(
            item for item in templates if item.insurer_code == insurer_code
        )
        if not templates:
            raise ColectivosServiceError(
                "template_unavailable",
                "La aseguradora solicitada no está disponible para este ramo.",
            )
    for template in templates:
        template_rows = rows if any("{row}" in field.position for field in template.fields) else ()
        capacity = template.end_row - template.start_row + 1
        if (
            len(template_rows) > capacity
            and not template.supports_chunking
            and not template.expandable_rows
        ):
            errors.append((template.insurer_name, "capacidad"))
            continue
        chunks = (template_rows,) if template.expandable_rows else (
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
    archive_name = (
        f"Invitaciones_{insurer_code}_{detail.branch_code}.zip"
        if insurer_code else f"Invitaciones_{detail.branch_code}.zip"
    )
    return archive.getvalue(), archive_name, "application/zip", tuple(errors)
