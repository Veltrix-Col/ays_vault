API_VERSION = "v8"
USER_AGENT = "A&S-Banco-Herramientas/1.0"
DEFAULT_SCOPES = (
    "ZohoCRM.org.READ",
    "ZohoCRM.settings.modules.READ",
    "ZohoCRM.settings.fields.READ",
    "ZohoCRM.modules.READ",
    "ZohoCRM.coql.READ",
)
MAX_RECORDS_PER_REQUEST = 200
MAX_FIELDS_PER_REQUEST = 50
MAX_COQL_LENGTH = 10_000
MAX_COQL_RECORDS = 2_000
TOKEN_EXPIRY_MARGIN_SECONDS = 60
