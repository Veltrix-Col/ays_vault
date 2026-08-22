from __future__ import annotations

from dataclasses import dataclass

from django.http import Http404


@dataclass(frozen=True)
class FieldSchema:
    key: str
    label: str
    kind: str = "text"
    required: bool = True
    choices: tuple[str, ...] = ()
    help_text: str = ""
    show_when: tuple[str, str] = ()


@dataclass(frozen=True)
class RepeatableSchema:
    key: str
    singular: str
    plural: str
    add_label: str
    fields: tuple[FieldSchema, ...]
    minimum: int = 1
    maximum: int = 20


@dataclass(frozen=True)
class BranchSchema:
    code: str
    slug: str
    name: str
    description: str
    icon_path: str
    fields: tuple[FieldSchema, ...]
    repeatables: tuple[RepeatableSchema, ...]
    documents: tuple[str, ...] = ()
    version: int = 1


IDENTIFICATION_CHOICES = ("CC", "CE", "TI", "RC", "Pasaporte", "NIT")
GENDER_CHOICES = ("Femenino", "Masculino", "Otro", "Prefiero no indicar")
ROLE_CHOICES = ("Afiliado principal", "Asegurado", "Cónyuge", "Hijo(a)", "Otro familiar")
VEHICLE_CLASS_CHOICES = (
    "Automovil",
    "Motocicleta",
    "Camiones y transporte de carga",
    "Transporte publico pasajeros",
    "Vehiculos especiales",
)

REQUESTER_FIELDS = (
    # Los registros nuevos capturan la identidad estructurada.  requester_name
    # sólo se conserva al leer contextos/snapshots históricos.
    FieldSchema("first_name", "Nombres"),
    FieldSchema("last_name", "Apellidos"),
    FieldSchema("requester_id_type", "Tipo de identificación", "choice", choices=IDENTIFICATION_CHOICES),
    FieldSchema("requester_document", "Identificación", "document"),
    FieldSchema("requester_birth_date", "Fecha de nacimiento", "date"),
    FieldSchema("requester_email", "Correo electrónico", "email"),
    FieldSchema("requester_phone", "Teléfono", "tel"),
    FieldSchema("collective_context", "Colectiva o tomador", required=False, help_text="Se precarga cuando el enlace se genera desde un cliente."),
)

PERSON_FIELDS = (
    FieldSchema("name", "Nombre"),
    FieldSchema("id_type", "Tipo de identificación", "choice", choices=IDENTIFICATION_CHOICES),
    FieldSchema("document", "Identificación", "document"),
    FieldSchema("birth_date", "Fecha de nacimiento", "date"),
    FieldSchema("gender", "Género", "choice", required=False, choices=GENDER_CHOICES),
    FieldSchema("relationship", "Parentesco o relación"),
    FieldSchema("role", "Rol", "choice", required=False, choices=ROLE_CHOICES),
    FieldSchema("plan_interest", "Plan o interés", required=False),
)

HEALTH_PERSON_FIELDS = (
    FieldSchema("is_requester", "Esta persona es el solicitante", "checkbox", required=False),
    FieldSchema("first_name", "Nombres"),
    FieldSchema("last_name", "Apellidos"),
    FieldSchema("id_type", "Tipo de identificación", "choice", choices=IDENTIFICATION_CHOICES),
    FieldSchema("document", "Identificación", "document"),
    FieldSchema("birth_date", "Fecha de nacimiento", "date"),
    FieldSchema("email", "Correo electrónico", "email"),
    FieldSchema("phone", "Teléfono", "tel"),
    FieldSchema("gender", "Género", "choice", required=False, choices=GENDER_CHOICES),
    FieldSchema("employment_relationship", "Vínculo con el fondo", "choice", choices=("Empleado", "Grupo familiar")),
    FieldSchema("relationship", "Parentesco o relación"),
    FieldSchema("currently_health_insured", "¿Tiene cobertura de salud vigente?", "choice", choices=("Sí", "No")),
    FieldSchema("current_health_insurer", "Aseguradora actual", required=False, show_when=("currently_health_insured", "Sí")),
    FieldSchema("current_health_policy_end", "Fin de la cobertura actual", "date", required=False, show_when=("currently_health_insured", "Sí")),
    FieldSchema("plan_interest", "Plan o interés", required=False),
)

VEHICLE_FIELDS = (
    FieldSchema("zero_km", "¿Vehículo 0 km?", "choice", choices=("Sí", "No")),
    FieldSchema(
        "plate", "Placa", required=False,
        help_text="Opcional si el vehículo aún no tiene placa asignada.",
    ),
    FieldSchema("brand", "Marca", required=False),
    FieldSchema("line", "Línea o referencia", required=False),
    FieldSchema("displacement", "Cilindraje", required=False),
    FieldSchema("model", "Modelo"),
    FieldSchema("class", "Clase", "choice", choices=VEHICLE_CLASS_CHOICES),
    FieldSchema("city", "Ciudad", required=False),
    FieldSchema("use", "Uso", required=False),
    FieldSchema("armored", "¿Vehículo blindado?", "choice", required=False, choices=("Sí", "No")),
    FieldSchema("currently_insured", "¿Actualmente asegurado?", "choice", required=False, choices=("Sí", "No")),
    # insured_name/insured_is_different se conservan para leer snapshots
    # históricos. Los registros nuevos usan una relación explícita y datos
    # estructurados cuando el asegurado no es el solicitante.
    FieldSchema("insured_same_as_requester", "El asegurado del vehículo es el mismo solicitante", "checkbox", required=False),
    FieldSchema("insured_id_type", "Tipo de identificación del asegurado", "choice", choices=IDENTIFICATION_CHOICES),
    FieldSchema("insured_document", "Identificación del asegurado", "document"),
    FieldSchema("insured_first_name", "Nombres del asegurado", required=False),
    FieldSchema("insured_last_name", "Apellidos del asegurado", required=False),
    FieldSchema("insured_birth_date", "Fecha de nacimiento del asegurado", "date", required=False),
    FieldSchema("insured_email", "Correo del asegurado", "email", required=False),
    FieldSchema("insured_phone", "Teléfono del asegurado", "tel", required=False),
)


BRANCH_SCHEMAS = (
    BranchSchema(
        "40", "movilidad", "Movilidad / Autos",
        "Cotice uno o varios vehículos dentro del contexto de una colectiva.",
        "M4 5h16v12H4zM7 17l1-3h8l1 3M7 10h10l-1-3H8z",
        REQUESTER_FIELDS,
        (RepeatableSchema("vehicles", "vehículo", "Vehículos", "Agregar vehículo", VEHICLE_FIELDS),),
        ("Matrícula o tarjeta de propiedad", "Otros soportes"),
        version=2,
    ),
    BranchSchema(
        "SALUD", "salud", "Salud",
        "Registre al afiliado principal y las personas de su grupo familiar.",
        "M12 21s-7-4.4-7-10a4 4 0 0 1 7-2 4 4 0 0 1 7 2c0 5.6-7 10-7 10z",
        REQUESTER_FIELDS,
        (RepeatableSchema("people", "persona", "Personas", "Agregar persona", HEALTH_PERSON_FIELDS),),
        ("Otros soportes solicitados por A&S",),
        version=3,
    ),
    BranchSchema(
        "VIDA", "vida", "Vida",
        "Capture la información básica de una o varias personas por asegurar.",
        "M12 21s-7-4.4-7-10a4 4 0 0 1 7-2 4 4 0 0 1 7 2c0 5.6-7 10-7 10z",
        REQUESTER_FIELDS,
        (RepeatableSchema("people", "asegurado", "Asegurados", "Agregar asegurado", PERSON_FIELDS + (FieldSchema("economic_activity", "Actividad económica", required=False),)),),
        ("Otros soportes para cotización",),
    ),
    BranchSchema(
        "EXEQUIAL", "exequial", "Exequial",
        "Registre al afiliado y los integrantes de su grupo familiar.",
        "M4 19h16M6 19V8l6-4 6 4v11M9 12h6",
        REQUESTER_FIELDS,
        (RepeatableSchema("people", "persona", "Grupo familiar", "Agregar persona", PERSON_FIELDS),),
        ("Otros soportes solicitados por A&S",),
    ),
    BranchSchema(
        "SOAT", "soat", "SOAT",
        "Diferencie al afiliado, al asegurado y el vehículo que se desea cotizar.",
        "M4 5h16v12H4zM7 17l1-3h8l1 3M7 10h10l-1-3H8z",
        REQUESTER_FIELDS + (
            FieldSchema("affiliate_name", "Nombre del afiliado"),
            FieldSchema("affiliate_id_type", "Tipo de identificación del afiliado", "choice", choices=IDENTIFICATION_CHOICES),
            FieldSchema("affiliate_document", "Identificación del afiliado", "document"),
            FieldSchema("insured_name", "Nombre del asegurado"),
            FieldSchema("insured_id_type", "Tipo de identificación del asegurado", "choice", choices=IDENTIFICATION_CHOICES),
            FieldSchema("insured_document", "Identificación del asegurado", "document"),
        ),
        (RepeatableSchema("vehicles", "vehículo", "Vehículos", "Agregar vehículo", VEHICLE_FIELDS),),
        ("Matrícula o tarjeta de propiedad", "Otros soportes"),
    ),
)

_BY_SLUG = {item.slug: item for item in BRANCH_SCHEMAS}
_POLICY_BRANCH_TO_SCHEMA = {
    "40": "movilidad",
    "83": "vida",
    "86": "exequial",
    "91": "salud",
}


def get_branch_schema(slug: str) -> BranchSchema:
    try:
        return _BY_SLUG[slug]
    except KeyError as exc:
        raise Http404("Ramo no disponible") from exc


def get_policy_branch_schema(branch_code: str, branch_name: str = "") -> BranchSchema:
    """Resolve the form from the already-open policy; never from browser input."""

    slug = _POLICY_BRANCH_TO_SCHEMA.get(str(branch_code or "").strip())
    if slug is None and "soat" in str(branch_name or "").strip().casefold():
        slug = "soat"
    if slug is None:
        raise Http404("El ramo de esta póliza todavía no tiene cotización individual.")
    return get_branch_schema(slug)
