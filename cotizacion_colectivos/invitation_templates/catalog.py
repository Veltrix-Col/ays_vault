from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


SOURCE_DIR = Path(__file__).resolve().parent / "source"


@dataclass(frozen=True)
class TemplateField:
    destination: str
    sheet: str
    position: str
    source: str
    transformation: str = "Texto sin transformación"
    automatic: bool = True
    required: bool = False
    observation: str = ""


@dataclass(frozen=True)
class InvitationTemplate:
    code: str
    insurer_code: str
    insurer_name: str
    branch_code: str
    branch_name: str
    purpose: str
    filename: str
    extension: str
    version: str
    active: bool
    generator: str
    data_sheet: str
    start_row: int
    end_row: int
    fields: tuple[TemplateField, ...]
    limitation: str = ""
    clear_cells: tuple[str, ...] = ()
    supports_chunking: bool = False

    @property
    def path(self) -> Path:
        return SOURCE_DIR / self.filename


AUTOS_SURA_FIELDS = (
    TemplateField("Placa", "Riesgos", "A{row}", "vehicle.plate", required=True),
    TemplateField("Modelo", "Riesgos", "B{row}", "vehicle.model", required=True),
    TemplateField("Fasecolda", "Riesgos", "C{row}", "manual.fasecolda", automatic=False, required=True),
    TemplateField("Marca", "Riesgos", "D{row}", "vehicle.brand"),
    TemplateField("Servicio", "Riesgos", "G{row}", "vehicle.use", required=True),
    TemplateField("Ciudad", "Riesgos", "H{row}", "vehicle.city", required=True),
    TemplateField("Plan", "Riesgos", "L{row}", "manual.insurer_plan", automatic=False, required=True),
    TemplateField("Tipo identificación asegurado", "Riesgos", "P{row}", "insured.id_type", required=True),
    TemplateField("ID asegurado", "Riesgos", "Q{row}", "insured.document", required=True),
    TemplateField("Nombre asegurado", "Riesgos", "R{row}", "insured.name", required=True, observation="Para persona natural."),
    TemplateField("Razón social asegurado", "Riesgos", "S{row}", "manual.legal_name", automatic=False, observation="Completar cuando el asegurado sea persona jurídica."),
    TemplateField("Ciudad asegurado", "Riesgos", "T{row}", "holder.city"),
)


AUTOS_GENERAL_FIELDS = (
    TemplateField("Tomador", "Formato Solicitud Cotización", "B5", "policy.holder", required=True),
    TemplateField("Identificación tomador", "Formato Solicitud Cotización", "B6", "holder.document", observation="La etiqueta maestra dice NIT; verificar manualmente para persona natural."),
    TemplateField("Inicio colectivo", "Formato Solicitud Cotización", "B8", "policy.start_date"),
    TemplateField("Aseguradora actual", "Formato Solicitud Cotización", "B9", "policy.current_insurer"),
    TemplateField("Forma de pago", "Formato Solicitud Cotización", "B14", "policy.payment_mode", observation="Solo se precarga si coincide con una opción de la maestra."),
    TemplateField("Documento voluntario", "Datos Vehículos a cotizar", "A{row}", "insured.document"),
    TemplateField("Placa", "Datos Vehículos a cotizar", "D{row}", "vehicle.plate", required=True),
    TemplateField("Fasecolda", "Datos Vehículos a cotizar", "E{row}", "manual.fasecolda", automatic=False, required=True),
    TemplateField("Modelo", "Datos Vehículos a cotizar", "F{row}", "vehicle.model", required=True),
    TemplateField("Zona de circulación", "Datos Vehículos a cotizar", "J{row}", "vehicle.city"),
    TemplateField("Uso", "Datos Vehículos a cotizar", "K{row}", "vehicle.use"),
    TemplateField("Relación con tomador", "Datos Vehículos a cotizar", "L{row}", "insured.relationship"),
)


SURA_VG_ANALYSIS_FIELDS = (
    TemplateField("Número de póliza", "Plantilla Cargas Masivas", "A{row}", "policy.full_reference", required=True),
    TemplateField("Operación", "Plantilla Cargas Masivas", "B{row}", "manual.operation", automatic=False, required=True),
    TemplateField("Fecha movimiento", "Plantilla Cargas Masivas", "C{row}", "manual.effective_date", automatic=False, required=True),
    TemplateField("Tipo ID afiliado", "Plantilla Cargas Masivas", "D{row}", "associate.id_type"),
    TemplateField("ID afiliado", "Plantilla Cargas Masivas", "E{row}", "associate.document"),
    TemplateField("Tipo ID asegurado", "Plantilla Cargas Masivas", "F{row}", "insured.id_type", required=True),
    TemplateField("ID asegurado", "Plantilla Cargas Masivas", "G{row}", "insured.document", required=True),
    TemplateField("Nombre asegurado", "Plantilla Cargas Masivas", "H{row}", "insured.name", required=True),
    TemplateField("Género", "Plantilla Cargas Masivas", "I{row}", "manual.gender", automatic=False),
    TemplateField("Fecha nacimiento", "Plantilla Cargas Masivas", "J{row}", "manual.birth_date", automatic=False),
    TemplateField("Parentesco", "Plantilla Cargas Masivas", "K{row}", "insured.relationship"),
    TemplateField("Valores asegurados", "Plantilla Cargas Masivas", "L{row}:P{row}", "member.economic_values", observation="Requiere equivalencia de cobertura confirmada."),
    TemplateField("Gestor/subgrupo/riesgo", "Plantilla Cargas Masivas", "Q{row}:S{row}", "manual.insurer_structure", automatic=False),
    TemplateField("Dependencia/nómina/crédito", "Plantilla Cargas Masivas", "T{row}:V{row}", "manual.employer_data", automatic=False),
    TemplateField("Extraprimas", "Plantilla Cargas Masivas", "W{row}:AC{row}", "manual.underwriting", automatic=False),
    TemplateField("Tipo ID beneficiario", "Plantilla Cargas Masivas", "AD{row}", "beneficiary.id_type"),
    TemplateField("ID beneficiario", "Plantilla Cargas Masivas", "AE{row}", "beneficiary.document"),
    TemplateField("Nombre beneficiario", "Plantilla Cargas Masivas", "AF{row}", "beneficiary.name"),
    TemplateField("Parentesco beneficiario", "Plantilla Cargas Masivas", "AG{row}", "beneficiary.relationship"),
    TemplateField("Distribución/contingencia", "Plantilla Cargas Masivas", "AH{row}:AI{row}", "manual.beneficiary_distribution", automatic=False),
    TemplateField("Datos bancarios", "Plantilla Cargas Masivas", "AJ{row}:AM{row}", "manual.bank_data", automatic=False),
    TemplateField("Datos de contacto", "Plantilla Cargas Masivas", "AN{row}:AP{row}", "member.contact"),
    TemplateField("Diagnóstico/exclusiones/radicado", "Plantilla Cargas Masivas", "AQ{row}:AV{row}", "manual.underwriting", automatic=False),
)


ALLIANZ_VG_FIELDS = (
    TemplateField("Nombre tomador", "FORMATO", "B10", "policy.holder", required=True),
    TemplateField("Identificación tomador", "FORMATO", "B11", "holder.document", required=True),
    TemplateField("Ubicación tomador", "FORMATO", "B13", "holder.city"),
    TemplateField("Inicio de vigencia", "FORMATO", "B25", "policy.start_date"),
    TemplateField("Fin de vigencia", "FORMATO", "B26", "policy.end_date"),
    TemplateField("Compañía actual", "FORMATO", "B77", "policy.current_insurer"),
    TemplateField("Procedimiento", "FORMATO", "B5", "manual.procedure", automatic=False, required=True),
    TemplateField("Clase de póliza", "FORMATO", "B6", "manual.policy_class", automatic=False, required=True),
    TemplateField("Actividad económica", "FORMATO", "B12", "manual.economic_activity", automatic=False),
    TemplateField("Característica y ocupación del grupo", "FORMATO", "B17:B20", "manual.group", automatic=False, required=True),
    TemplateField("Número y edades de personas", "FORMATO", "B21:B23", "manual.demographics", automatic=False, required=True),
    TemplateField("Datos de intermediario", "FORMATO", "B30:B34", "manual.intermediary", automatic=False, required=True),
    TemplateField("Valores asegurados", "FORMATO", "B38:B53", "manual.insured_values", automatic=False, required=True),
    TemplateField("Amparos", "FORMATO", "B56:B72", "manual.coverages", automatic=False, required=True),
    TemplateField("Condiciones particulares", "FORMATO", "B76:B82", "manual.conditions", automatic=False),
    TemplateField("Siniestralidad", "FORMATO", "A85:C87", "manual.claims", automatic=False),
)

ALLIANZ_VG_CLEAR_CELLS = tuple(
    "B5 B6 B10 B11 B12 B13 B17 B18 B19 B20 B21 B22 B23 B25 B26 "
    "B30 B31 B32 B33 B34 B38 B42 B43 B44 B45 B46 B47 B48 B49 B50 B51 "
    "B56 B57 B58 B59 B60 B61 B62 B63 B64 B67 B68 B69 B70 B71 B72 "
    "B76 B77 B78 B79 B80 B81 B82 A85 B85 C85 A86 B86 C86 A87 B87 C87 B93"
    .split()
)


INVITATION_TEMPLATE_CATALOG = (
    InvitationTemplate(
        code="sura_vg_mass_biff8", insurer_code="SURA", insurer_name="SURA",
        branch_code="83", branch_name="Vida grupo deudores",
        purpose="Carga masiva de asegurados y beneficiarios de Vida Grupo",
        filename="vida/sura/Plantilla Carga Masiva Sura_VG.xls", extension="xls",
        version="maestra-biff8", active=False, generator="unsupported_biff8",
        data_sheet="Plantilla Cargas Masivas", start_row=14, end_row=53,
        fields=SURA_VG_ANALYSIS_FIELDS,
        limitation=(
            "BIFF8 no puede editarse de forma portable con las dependencias del proyecto "
            "con preservación demostrable de estilos y estructura. No se convierte a XLSX."
        ),
    ),
    InvitationTemplate(
        code="allianz_vg_collective", insurer_code="ALLIANZ", insurer_name="Allianz",
        branch_code="83", branch_name="Vida grupo deudores",
        purpose="Solicitud de cotización de Vida Grupo colectiva",
        filename="vida/allianz/Formato Vida Grupo Colectiva_Allianz_EDM.xlsx",
        extension="xlsx", version="2026-04-10", active=True,
        generator="ooxml_patch", data_sheet="FORMATO", start_row=1, end_row=1,
        fields=ALLIANZ_VG_FIELDS,
        limitation=(
            "Solo se precargan tomador, identificación, ubicación, vigencias y compañía actual. "
            "Coberturas, valores, siniestralidad e intermediación permanecen manuales."
        ),
        clear_cells=ALLIANZ_VG_CLEAR_CELLS,
    ),
    InvitationTemplate(
        code="sura_autos_quote", insurer_code="SURA", insurer_name="SURA",
        branch_code="40", branch_name="Movilidad colectivo",
        purpose="Cotización de automóviles",
        filename="movilidad/sura/Plantilla cotizacion Autos_Sura.xlsx", extension="xlsx",
        version="2026-07-14", active=True, generator="ooxml_patch",
        data_sheet="Riesgos", start_row=2, end_row=22,
        fields=AUTOS_SURA_FIELDS, supports_chunking=True,
    ),
    InvitationTemplate(
        code="allianz_autos_collective", insurer_code="ALLIANZ",
        insurer_name="Allianz", branch_code="40",
        branch_name="Movilidad colectivo",
        purpose="Solicitud general de cotización de Autos colectivo",
        filename="movilidad/allianz/Plantilla Solicitud Cotizaciones_Autos colectivo.xlsx",
        extension="xlsx", version="maestra-repositorio", active=True,
        generator="ooxml_patch", data_sheet="Datos Vehículos a cotizar",
        start_row=2, end_row=300, fields=AUTOS_GENERAL_FIELDS,
        limitation="La aseguradora se confirmó en propiedades internas del libro, no por el nombre del archivo.",
        supports_chunking=True,
    ),
)


def templates_for_branch(branch_code: str, *, active_only: bool = False):
    result = tuple(item for item in INVITATION_TEMPLATE_CATALOG if item.branch_code == branch_code)
    return tuple(item for item in result if item.active) if active_only else result
