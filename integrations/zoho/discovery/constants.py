from __future__ import annotations

SCHEMA_VERSION = 2
ALLOWED_PROFILES = frozenset({"sandbox", "production"})

SNAPSHOT_FILES = (
    "organization.json",
    "modules.json",
    "fields.json",
    "layouts.json",
    "relationships.json",
    "related_lists.json",
    "subforms.json",
    "picklists.json",
    "errors.json",
)

SECRET_KEY_PARTS = (
    "access_token",
    "refresh_token",
    "client_secret",
    "client_id",
    "authorization",
    "cookie",
    "password",
    "private_key",
)

MODULE_KEYS = (
    "label",
    "plural_label",
    "singular_label",
    "api_name",
    "id",
    "module_name",
    "generated_type",
    "module_type",
    "visibility",
    "user_hidden",
    "api_supported",
    "webform_supported",
    "feeds_required",
    "scoring_supported",
    "business_card_field_limit",
    "parent_module",
    "sequence_number",
    "status",
    "custom_module",
)

FIELD_KEYS = (
    "field_id",
    "id",
    "field_label",
    "display_label",
    "api_name",
    "data_type",
    "length",
    "decimal_place",
    "required",
    "read_only",
    "visible",
    "unique",
    "system_mandatory",
    "custom_field",
    "field_read_only",
    "sequence_number",
    "created_source",
    "businesscard_supported",
    "mass_update",
    "quick_create",
    "lookup",
    "related_details",
    "subform",
    "associated_module",
    "pick_list_values",
    "picklist_values",
    "crypt",
    "formula",
    "currency",
    "operation_type",
)
