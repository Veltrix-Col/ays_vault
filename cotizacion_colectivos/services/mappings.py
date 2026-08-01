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
    "Riesgo",
    "Estado",
    "Ramo",
    "Aseguradora",
    "Fecha_ingreso_riesgo",
    "Fecha_salida_riesgo",
)

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
)

RISK_DETAIL_FIELDS = (
    "id",
    "Name",
    "Tipo_de_riesgo",
    "Fecha_inicio",
    "Fecha_fin",
    "Layout",
)

# Evidencia real del 31-07-2026: 69/69, 15/15 y 4/4 IDs coincidentes.
CONFIRMED_RELATIONS = frozenset(
    {
        "Riesgos1.Asegurado->Contacts",
        "Riesgos1.P_liza->Polizas",
        "Riesgos1.Riesgo->Riesgos",
    }
)
