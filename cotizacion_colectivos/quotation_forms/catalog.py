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

REQUESTER_FIELDS = (
    FieldSchema("requester_name", "Nombre del solicitante"),
    FieldSchema("requester_id_type", "Tipo de identificación", "choice", choices=IDENTIFICATION_CHOICES),
    FieldSchema("requester_document", "Identificación", "document"),
    FieldSchema("requester_email", "Correo electrónico", "email"),
    FieldSchema("requester_phone", "Teléfono", "tel"),
    FieldSchema("collective_context", "Colectiva o tomador", required=False, help_text="Se precarga cuando el enlace se genera desde un cliente."),
)

PERSON_FIELDS = (
    FieldSchema("name", "Nombre"),
    FieldSchema("id_type", "Tipo de identificación", "choice", choices=IDENTIFICATION_CHOICES),
    FieldSchema("document", "Identificación", "document"),
    FieldSchema("birth_date", "Fecha de nacimiento", "date"),
    FieldSchema("gender", "Género", "choice", choices=GENDER_CHOICES),
    FieldSchema("relationship", "Parentesco o relación"),
    FieldSchema("role", "Rol", "choice", choices=ROLE_CHOICES),
)

VEHICLE_FIELDS = (
    FieldSchema("plate", "Placa"),
    FieldSchema("brand", "Marca", required=False),
    FieldSchema("line", "Línea o referencia", required=False),
    FieldSchema("model", "Modelo"),
    FieldSchema("city", "Ciudad"),
    FieldSchema("use", "Uso"),
    FieldSchema("insured_name", "Nombre del asegurado"),
    FieldSchema("insured_id_type", "Tipo de identificación del asegurado", "choice", choices=IDENTIFICATION_CHOICES),
    FieldSchema("insured_document", "Identificación del asegurado", "document"),
)


BRANCH_SCHEMAS = (
    BranchSchema(
        "40", "movilidad", "Movilidad / Autos",
        "Cotice uno o varios vehículos dentro del contexto de una colectiva.",
        "M4 5h16v12H4zM7 17l1-3h8l1 3M7 10h10l-1-3H8z",
        REQUESTER_FIELDS,
        (RepeatableSchema("vehicles", "vehículo", "Vehículos", "Agregar vehículo", VEHICLE_FIELDS),),
        ("Matrícula o tarjeta de propiedad", "Otros soportes"),
    ),
    BranchSchema(
        "SALUD", "salud", "Salud",
        "Registre al afiliado principal y las personas de su grupo familiar.",
        "M12 21s-7-4.4-7-10a4 4 0 0 1 7-2 4 4 0 0 1 7 2c0 5.6-7 10-7 10z",
        REQUESTER_FIELDS,
        (RepeatableSchema("people", "persona", "Personas", "Agregar persona", PERSON_FIELDS),),
        ("Otros soportes solicitados por A&S",),
    ),
    BranchSchema(
        "VIDA", "vida", "Vida",
        "Capture la información básica de una o varias personas por asegurar.",
        "M12 21s-7-4.4-7-10a4 4 0 0 1 7-2 4 4 0 0 1 7 2c0 5.6-7 10-7 10z",
        REQUESTER_FIELDS,
        (RepeatableSchema("people", "asegurado", "Asegurados", "Agregar asegurado", PERSON_FIELDS + (FieldSchema("economic_activity", "Actividad económica", required=False),)),),
        ("Declaración de asegurabilidad, cuando aplique", "Otros soportes"),
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


def get_branch_schema(slug: str) -> BranchSchema:
    try:
        return _BY_SLUG[slug]
    except KeyError as exc:
        raise Http404("Ramo no disponible") from exc
