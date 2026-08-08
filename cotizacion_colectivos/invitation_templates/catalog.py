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


INVITATION_TEMPLATE_CATALOG = (
    InvitationTemplate(
        code="sura_vg_mass_biff8", insurer_code="SURA", insurer_name="SURA",
        branch_code="83", branch_name="Vida grupo deudores",
        purpose="Carga masiva de asegurados y beneficiarios de Vida Grupo",
        filename="Plantilla Carga Masiva Sura_VG.xls", extension="xls",
        version="maestra-biff8", active=False, generator="unsupported_biff8",
        data_sheet="Plantilla Cargas Masivas", start_row=14, end_row=53,
        fields=SURA_VG_ANALYSIS_FIELDS,
        limitation=(
            "BIFF8 no puede editarse de forma portable con las dependencias del proyecto "
            "con preservación demostrable de estilos y estructura. No se convierte a XLSX."
        ),
    ),
    InvitationTemplate(
        code="sura_autos_quote", insurer_code="SURA", insurer_name="SURA",
        branch_code="40", branch_name="Movilidad colectivo",
        purpose="Cotización de automóviles",
        filename="Plantilla cotizacion Autos_Sura.xlsx", extension="xlsx",
        version="2026-07-14", active=True, generator="ooxml_patch",
        data_sheet="Riesgos", start_row=2, end_row=22,
        fields=AUTOS_SURA_FIELDS,
    ),
    InvitationTemplate(
        code="allianz_autos_collective", insurer_code="ALLIANZ",
        insurer_name="Allianz", branch_code="40",
        branch_name="Movilidad colectivo",
        purpose="Solicitud general de cotización de Autos colectivo",
        filename="Plantilla Solicitud Cotizaciones_Autos colectivo.xlsx",
        extension="xlsx", version="maestra-repositorio", active=True,
        generator="ooxml_patch", data_sheet="Datos Vehículos a cotizar",
        start_row=2, end_row=300, fields=AUTOS_GENERAL_FIELDS,
        limitation="La aseguradora se confirmó en propiedades internas del libro, no por el nombre del archivo.",
    ),
)


def templates_for_branch(branch_code: str, *, active_only: bool = False):
    result = tuple(item for item in INVITATION_TEMPLATE_CATALOG if item.branch_code == branch_code)
    return tuple(item for item in result if item.active) if active_only else result
