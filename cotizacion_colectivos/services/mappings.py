CONTACTS_MODULE = "Contacts"
POLICIES_MODULE = "Polizas"
INSURED_MODULE = "Riesgos1"
RISKS_MODULE = "Riesgos"

COMPANY_TYPE = "Persona jurídica"
PERSON_TYPE = "Persona natural"
COMPANY_ID_TYPE = "NIT"
PERSON_ID_TYPE = "CC"

SEARCH_LIMIT = 20
RELATION_LIMIT = 20

CONTACT_SEARCH_FIELDS = (
    "id",
    "Tipo_de_persona",
    "Tipo_ID",
    "N_mero_de_ID",
    "First_Name",
    "Last_Name",
    "Full_Name",
    "Raz_n_social",
    "Nombre_comercial",
    "Estado",
)

CONTACT_DETAIL_FIELDS = CONTACT_SEARCH_FIELDS + (
    "Email",
    "Phone",
    "Mobile",
    "Direcci_n",
    "Ciudad_de_direcci_n_principal",
    "Empresa",
)

INSURED_RELATION_FIELDS = (
    "id",
    "Name",
    "P_liza",
    "Asegurado",
    "Contacto_facturaci_n_dividida_colectivas",
    "Beneficiario",
    "Riesgo",
    "Estado",
    "Ramo",
    "Aseguradora",
    "Fecha_ingreso_riesgo",
    "Fecha_salida_riesgo",
    "Plan",
    "Parentesco",
    "Email",
    "Correo_electr_nico_afiliado",
    "Prima",
    "Pago_total",
    "Pago_total_Seg_n_la_forma_de_pago_Valor_asegura",
    "Pago_EMPLEADO_Sin_IVA",
    "Valor_asegurado",
    "Observaciones",
)

# Lookups de rol confirmados por metadata y por el perfilado real agregado.
INSURED_CONTACT_ROLES = (
    ("Asegurado", "Asegurado"),
    ("Contacto_facturaci_n_dividida_colectivas", "Afiliado"),
    ("Beneficiario", "Beneficiario"),
)

# Catálogos cerrados tomados de fields.json. Plan permanece libre porque es text.
CONTACT_ID_TYPE_CHOICES = ("CC", "CE", "RC", "TI", "PP", "PEP", "EX", "NUIP", "PPT", "NIT")
RELATIONSHIP_CHOICES = (
    "Afiliado", "Abuelo", "Cónyuge", "Compañero permanente", "Cuñado",
    "Exesposo", "Hermano", "Hijo", "Nieto", "Novio", "Primo",
    "Progenitor", "Sobrino", "Suegro", "Tio", "Yerno/Nuera", "Otro",
)
RELATION_ROLE_CHOICES = tuple(label for _field, label in INSURED_CONTACT_ROLES)
INSURED_STATE_CHOICES = ("Activo", "Excluido", "Cancelado", "Congelado")

POLICY_DETAIL_FIELDS = (
    "id",
    "Name",
    "Tomador_principal1",
    "Estado_de_la_p_liza",
    "Ramo",
    "Aseguradora1",
    "P_liza_Fecha_de_inicio_vigencia",
    "P_liza_Fecha_fin_de_la_vigencia",
    "Layout",
    "Renovable",
    "Modo_de_pago",
    "Frecuencia",
    "Periodicidad_de_pago",
    "Medio_de_pago",
    "N_mero_de_cuotas",
    "Fecha_primera_cuota",
    "Pago_1",
    "Pago_2",
    "Pago_3",
    "Pago_4",
    "Pago_5",
    "Pago_6",
    "Pago_7",
    "Pago_8",
    "Pago_9",
    "Pago_10",
    "Pago_11",
    "Pago_12",
    "Valor_prima",
    "Pago_total",
    "Valor_asegurado",
    "Referencia_Plan",
    "Plan",
)

RISK_DETAIL_FIELDS = (
    "id",
    "Name",
    "Tipo_de_riesgo",
    "Fecha_inicio",
    "Fecha_fin",
    "Layout",
    "Ciudad",
    "Direccion",
    "A_o_construcci_n",
    "Tipo_de_uso",
    "Placa_del_vehiculo",
    "Marca_Tipo_Caracter_sticas",
    "Modelo",
)

# Evidencia real del 31-07-2026: 69/69, 15/15 y 4/4 IDs coincidentes.
CONFIRMED_RELATIONS = frozenset(
    {
        "Riesgos1.Asegurado->Contacts",
        "Riesgos1.P_liza->Polizas",
        "Riesgos1.Riesgo->Riesgos",
    }
)
