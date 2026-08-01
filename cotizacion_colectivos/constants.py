SANDBOX_PROFILE = "sandbox"
DEFAULT_PROBE_LIMIT = 3
MAX_PROBE_LIMIT = 10

CONTACTS_PROFILE_MODULE = "Contacts"
CONTACTS_PROFILE_FIELDS = (
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
    "Empresa",
)
CONTACTS_PROFILE_PAGE_SIZE = 200
CONTACTS_PROFILE_MAX_RECORDS = 20_000

RELATION_PROFILE_PAGE_SIZE = 200
RELATION_PROFILE_MAX_RECORDS = 20_000

POLICY_PROFILE_FIELDS = (
    "id",
    "Name",
    "Tomador_principal1",
    "Estado_de_la_p_liza",
    "Ramo",
    "Aseguradora1",
    "P_liza_Fecha_de_inicio_vigencia",
    "P_liza_Fecha_fin_de_la_vigencia",
    "P_liza_anterior",
    "Grupo_econ_mico",
)

INSURED_PROFILE_FIELDS = (
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

RISK_PROFILE_FIELDS = (
    "id",
    "Name",
    "Contratista",
    "Contratante",
    "Inmueble",
    "Tipo_de_riesgo",
    "Fecha_inicio",
    "Fecha_fin",
)

DISCOVERY_MODULES = (
    "Accounts",
    "Contacts",
    "Persona_juridica",
    "Polizas",
    "Riesgos",
    "Riesgos1",
    "Asegurados_Colectivas",
    "Asegurados_Greenland",
    "Coberturas_Asegurado",
    "Relacionados",
    "Relacionados_Hijoss",
    "Relaciones",
    "Opeeraciones",
    "Renovaciones_Vencimientos",
    "Directorio_Aseguradoras",
    "Ramo_12",
    "C_digos_Ramos",
    "Tipo_d_Contacto",
    "Informaci_n_de_contacto",
    "Informaci_n_familiar_y_co",
    "Empleados",
    "Vendedores",
)
