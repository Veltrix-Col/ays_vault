from __future__ import annotations

import copy
import json
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase

from integrations.zoho.discovery import (
    DiscoveryService,
    SnapshotStore,
    compare_snapshots,
    load_snapshot,
    render_comparison_markdown,
    render_model_markdown,
)
from integrations.zoho.discovery.normalization import safe_value
from integrations.zoho.discovery.service import (
    DiscoveryConfigurationError,
    DiscoveryFatalError,
)
from integrations.zoho.exceptions import ZohoAPIError
from integrations.zoho.schemas import FieldMetadata, ModuleMetadata


def modules():
    return (
        SimpleNamespace(
            api_name="Contacts", module_name="Contacts", plural_label="Personas",
            singular_label="Persona", id="m1", api_supported=True,
        ),
        SimpleNamespace(
            api_name="Polizas", module_name="Polizas", plural_label="Pólizas",
            singular_label="Póliza", id="m2", api_supported=True,
        ),
        SimpleNamespace(
            api_name="Restricted", module_name="Restricted", plural_label="Restringido",
            singular_label="Restringido", id="m3", api_supported=True,
        ),
    )


def contact_fields():
    return (
        SimpleNamespace(
            id="f1", api_name="Tipo", field_label="Tipo", data_type="picklist",
            required=False, read_only=False,
            pick_list_values=(
                {"display_value": "Natural", "actual_value": "natural", "sequence_number": 2, "active": True},
                {"display_value": "Jurídica", "actual_value": "juridica", "sequence_number": 1, "active": True},
            ),
        ),
        SimpleNamespace(
            id="f2", api_name="Relaciones", field_label="Relaciones", data_type="subform",
            subform={"module": {"api_name": "Relaciones_Subform", "id": "sm1"}},
        ),
    )


def policy_fields():
    return (
        SimpleNamespace(
            id="f3", api_name="Tomador", field_label="Tomador", data_type="lookup",
            required=True, read_only=False,
            lookup={"id": "lookup-1", "module": {"api_name": "Contacts", "id": "m1"}},
            related_details={"api_name": "Polizas_Tomador"},
        ),
        SimpleNamespace(
            id="f4", api_name="Sin_Destino", field_label="Sin destino", data_type="lookup",
            lookup={}, related_details={},
        ),
    )


def fake_facade(profile="sandbox", *, fields_failure=None, with_capabilities=True):
    def fields(module):
        if module == "Contacts":
            return contact_fields()
        if module == "Polizas":
            return policy_fields()
        if fields_failure:
            raise fields_failure
        return ()

    metadata = SimpleNamespace(list_modules=Mock(return_value=modules()), list_fields=Mock(side_effect=fields))
    if with_capabilities:
        metadata.list_layouts = Mock(side_effect=lambda module: (
            {"id": f"layout-{module}", "name": "Estándar", "visible": True,
             "sections": [{"name": "Principal"}], "fields": ["Tipo"], "profiles": []},
        ))
        metadata.list_related_lists = Mock(side_effect=lambda module: (
            {"name": "Relacionados", "api_name": "Related", "related_module": {"api_name": "Contacts"},
             "type": "default", "visible": True, "sequence_number": 1},
        ))
    organization = SimpleNamespace(
        organization_id=f"org-{profile}", company_name="A&S", environment=profile,
        country="Colombia", currency="COP", timezone="America/Bogota",
    )
    return SimpleNamespace(
        profile=profile, environment=profile, backend_name="sdk",
        organization=SimpleNamespace(get=Mock(return_value=organization)),
        metadata=metadata,
        records=Mock(), search=Mock(), coql=Mock(),
    )


def snapshot(profile="sandbox"):
    service = DiscoveryService(
        profile=profile, facade=fake_facade(profile),
        clock=lambda: datetime(2026, 8, 13, 12, 0, tzinfo=UTC),
    )
    return service.discover()


class DiscoveryServiceTests(SimpleTestCase):
    def test_real_scale_partial_snapshot_has_explicit_coverage_and_safe_errors(self):
        discovered_modules = tuple(
            SimpleNamespace(
                api_name=f"Module_{index:02d}", module_name=f"Module_{index:02d}",
                plural_label=f"Modules {index}", singular_label=f"Module {index}",
                id=f"m{index}", api_supported=True,
            )
            for index in range(89)
        )
        failure = ZohoAPIError(
            "raw response with secret", status_code=500,
            zoho_code="INTERNAL_ERROR", backend="sdk",
        )

        def list_fields(api_name):
            index = int(api_name.rsplit("_", 1)[1])
            if index >= 63:
                raise failure
            return ()

        facade = fake_facade(with_capabilities=False)
        facade.metadata.list_modules.return_value = discovered_modules
        facade.metadata.list_fields.side_effect = list_fields
        result = DiscoveryService(profile="sandbox", facade=facade).discover()
        manifest = result["manifest"]
        self.assertEqual(manifest["status"], "partial")
        self.assertEqual(manifest["modules_total"], 89)
        self.assertEqual(manifest["modules_fields_ok"], 63)
        self.assertEqual(manifest["modules_fields_failed"], 26)
        self.assertEqual(manifest["errors_total"], 28)
        self.assertNotIn("raw response", json.dumps(result))
        self.assertEqual(
            sum(item["fields_status"] == "error" for item in result["modules"]), 26
        )
        with TemporaryDirectory() as directory:
            publication = SnapshotStore(Path(directory)).save(result)
            self.assertTrue(publication["changed"])
            self.assertTrue((publication["path"] / "errors.json").is_file())
            persisted = load_snapshot(publication["path"])
            self.assertEqual(persisted["manifest"]["status"], "partial")
            self.assertEqual(len(persisted["errors"]), 28)

    def test_installed_sdk_dataclasses_are_normalized_without_invented_values(self):
        facade = fake_facade()
        facade.metadata.list_modules.return_value = (
            ModuleMetadata("Contacts", "Contacts", "Personas", "Persona", api_supported=True),
        )
        facade.metadata.list_fields.side_effect = None
        facade.metadata.list_fields.return_value = (
            FieldMetadata(
                "Tomador", "Tomador", "lookup", required=True,
                lookup={"module": {"api_name": "Contacts"}},
            ),
        )
        result = DiscoveryService(profile="sandbox", facade=facade).discover()
        self.assertEqual(result["modules"][0]["api_name"], "Contacts")
        self.assertNotIn("id", result["modules"][0])
        self.assertEqual(result["fields"][0]["api_name"], "Tomador")
        self.assertEqual(result["relationships"][0]["target_module_api_name"], "Contacts")

    def test_discovers_all_supported_metadata_and_preserves_unresolved_lookup(self):
        facade = fake_facade()
        result = DiscoveryService(profile="sandbox", facade=facade).discover()
        self.assertEqual([item["api_name"] for item in result["modules"]], ["Contacts", "Polizas", "Restricted"])
        self.assertEqual(result["manifest"]["modules_fields_ok"], 3)
        self.assertEqual(len(result["layouts"]), 3)
        self.assertEqual(len(result["related_lists"]), 3)
        self.assertEqual(result["subforms"][0]["subform_module_api_name"], "Relaciones_Subform")
        resolved = {item["source_field_api_name"]: item for item in result["relationships"]}
        self.assertEqual(resolved["Tomador"]["target_module_api_name"], "Contacts")
        self.assertTrue(resolved["Tomador"]["resolved"])
        self.assertFalse(resolved["Sin_Destino"]["resolved"])
        self.assertEqual(resolved["Sin_Destino"]["reason"], "metadata_target_missing")
        self.assertEqual([item["actual_value"] for item in result["picklists"]], ["juridica", "natural"])

    def test_permission_error_does_not_abort_other_modules(self):
        failure = ZohoAPIError(
            "secret response must not escape", status_code=403,
            zoho_code="NO_PERMISSION", backend="rest",
        )
        result = DiscoveryService(
            profile="sandbox", facade=fake_facade(fields_failure=failure)
        ).discover()
        self.assertEqual(result["manifest"]["modules_fields_ok"], 2)
        self.assertEqual(result["manifest"]["modules_fields_failed"], 1)
        error = next(item for item in result["errors"] if item["module"] == "Restricted")
        self.assertEqual(error["category"], "permission_denied")
        restricted = next(item for item in result["modules"] if item["api_name"] == "Restricted")
        self.assertEqual(restricted["fields_metadata_status"], "unavailable")
        self.assertEqual(restricted["fields_metadata_error"], "permission_denied")
        self.assertNotIn("secret response", json.dumps(result))
        self.assertEqual(result["manifest"]["status"], "partial")

    def test_server_error_after_sdk_retries_is_partial_and_next_modules_survive(self):
        failure = ZohoAPIError(
            "server body", status_code=500, zoho_code="INTERNAL_ERROR", backend="sdk"
        )
        facade = fake_facade(fields_failure=failure)
        result = DiscoveryService(profile="sandbox", facade=facade).discover()
        self.assertEqual(facade.metadata.list_fields.call_count, 3)
        self.assertEqual(result["manifest"]["modules_fields_ok"], 2)
        self.assertEqual(result["manifest"]["modules_fields_failed"], 1)
        self.assertEqual(result["manifest"]["status"], "partial")
        error = next(item for item in result["errors"] if item["module"] == "Restricted")
        self.assertEqual(error["status_code"], 500)
        self.assertNotIn("server body", json.dumps(result))

    def test_organization_and_modules_root_failures_are_fatal(self):
        organization_failure = fake_facade()
        organization_failure.organization.get.side_effect = ZohoAPIError(
            "secret", status_code=401, zoho_code="AUTHENTICATION_FAILURE", backend="sdk"
        )
        with self.assertRaisesMessage(DiscoveryFatalError, "organización"):
            DiscoveryService(profile="sandbox", facade=organization_failure).discover()

        modules_failure = fake_facade()
        modules_failure.metadata.list_modules.side_effect = ZohoAPIError(
            "secret", status_code=500, zoho_code="INTERNAL_ERROR", backend="sdk"
        )
        with self.assertRaisesMessage(DiscoveryFatalError, "módulos"):
            DiscoveryService(profile="sandbox", facade=modules_failure).discover()

    def test_unsupported_fields_api_is_safely_classified(self):
        failure = ZohoAPIError(
            "raw", status_code=400, zoho_code="INVALID_MODULE", backend="rest"
        )
        result = DiscoveryService(
            profile="production", facade=fake_facade("production", fields_failure=failure)
        ).discover()
        error = next(item for item in result["errors"] if item["module"] == "Restricted")
        self.assertEqual(error["category"], "api_not_supported")

    def test_missing_optional_capabilities_are_documented_without_private_backend(self):
        facade = fake_facade(with_capabilities=False)
        result = DiscoveryService(profile="sandbox", facade=facade).discover()
        self.assertEqual(result["layouts"], [])
        self.assertEqual(result["related_lists"], [])
        self.assertEqual(
            {item["endpoint_type"] for item in result["errors"] if item["category"] == "capability_unavailable"},
            {"layouts", "related_lists"},
        )

    def test_profile_is_explicit_and_environment_cannot_cross(self):
        with self.assertRaises(DiscoveryConfigurationError):
            DiscoveryService(profile="")
        with self.assertRaises(DiscoveryConfigurationError):
            DiscoveryService(profile="qa")
        with self.assertRaisesMessage(DiscoveryConfigurationError, "no coincide"):
            DiscoveryService(profile="sandbox", facade=fake_facade("production")).discover()

    @patch("integrations.zoho.discovery.service.get_zoho")
    def test_each_profile_is_passed_exactly_and_never_falls_back(self, get_zoho):
        get_zoho.side_effect = lambda profile: fake_facade(profile)
        DiscoveryService(profile="sandbox").discover()
        DiscoveryService(profile="production").discover()
        self.assertEqual(
            [call.kwargs["profile"] for call in get_zoho.call_args_list],
            ["sandbox", "production"],
        )

    def test_never_calls_records_search_coql_or_write_methods(self):
        facade = fake_facade()
        DiscoveryService(profile="sandbox", facade=facade).discover()
        facade.records.assert_not_called()
        facade.search.assert_not_called()
        facade.coql.assert_not_called()
        for name in ("create", "update", "delete", "upsert", "write", "upload", "attach"):
            self.assertFalse(hasattr(DiscoveryService, name))


class SnapshotStoreTests(SimpleTestCase):
    def test_snapshot_is_deterministic_profile_isolated_and_atomic(self):
        with TemporaryDirectory() as directory:
            store = SnapshotStore(Path(directory))
            placeholder = Path(directory) / "sandbox" / "latest"
            placeholder.mkdir(parents=True)
            (placeholder / ".gitkeep").write_text("", encoding="utf-8")
            first = snapshot("sandbox")
            result = store.save(first)
            self.assertTrue(result["changed"])
            loaded = load_snapshot(Path(directory) / "sandbox" / "latest")
            self.assertEqual(loaded["manifest"]["profile"], "sandbox")
            self.assertEqual(loaded["fields"], first["fields"])
            self.assertFalse(list(Path(directory).rglob("*.tmp")))
            production = snapshot("production")
            store.save(production)
            self.assertTrue((Path(directory) / "sandbox" / "latest").exists())
            self.assertTrue((Path(directory) / "production" / "latest").exists())

    def test_identical_snapshot_does_not_rewrite_or_create_history(self):
        with TemporaryDirectory() as directory:
            store = SnapshotStore(Path(directory))
            original = snapshot()
            store.save(original)
            manifest = Path(directory) / "sandbox" / "latest" / "manifest.json"
            before = manifest.read_bytes()
            duplicate = snapshot()
            duplicate["manifest"]["generated_at"] = "2030-01-01T00:00:00+00:00"
            result = store.save(duplicate)
            self.assertFalse(result["changed"])
            self.assertEqual(manifest.read_bytes(), before)
            self.assertEqual(list((Path(directory) / "sandbox" / "history").glob("*")), [])

    def test_changed_snapshot_archives_previous_latest(self):
        with TemporaryDirectory() as directory:
            store = SnapshotStore(Path(directory))
            first = snapshot()
            store.save(first)
            changed = copy.deepcopy(first)
            changed["modules"].append({"api_name": "New", "module_name": "New", "label": "New"})
            changed["manifest"]["modules_total"] += 1
            changed["manifest"]["generated_at"] = "2026-08-14T12:00:00+00:00"
            result = store.save(changed)
            self.assertTrue(result["changed"])
            self.assertTrue(result["history_path"].is_dir())
            self.assertEqual(load_snapshot(Path(directory) / "sandbox" / "latest")["modules"][-1]["api_name"], "New")

    def test_failure_before_publish_preserves_latest_and_history_byte_for_byte(self):
        with TemporaryDirectory() as directory:
            store = SnapshotStore(Path(directory))
            store.save(snapshot())
            profile_root = Path(directory) / "sandbox"

            def tree_bytes():
                return {
                    str(path.relative_to(profile_root)): path.read_bytes()
                    for path in profile_root.rglob("*") if path.is_file()
                }

            before = tree_bytes()
            changed = snapshot()
            changed["errors"].append({
                "module": "X", "endpoint_type": "fields", "category": "server_error",
                "status_code": 500, "safe_message": "Metadata no disponible.",
            })
            changed["manifest"]["errors_total"] += 1
            with patch.object(store, "_write_directory", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    store.save(changed)
            self.assertEqual(tree_bytes(), before)

    def test_partial_then_success_archives_partial_and_identical_partial_does_not(self):
        with TemporaryDirectory() as directory:
            store = SnapshotStore(Path(directory))
            partial = snapshot()
            self.assertEqual(partial["manifest"]["status"], "partial")
            store.save(partial)
            duplicate = copy.deepcopy(partial)
            duplicate["manifest"]["generated_at"] = "2026-08-14T00:00:00+00:00"
            self.assertFalse(store.save(duplicate)["changed"])
            complete = copy.deepcopy(partial)
            complete["errors"] = []
            complete["manifest"]["status"] = "success"
            complete["manifest"]["errors_total"] = 0
            for module in complete["modules"]:
                module["fields_status"] = "ok"
                module["fields_metadata_status"] = "available"
                module.pop("fields_error_category", None)
                module.pop("fields_metadata_error", None)
            result = store.save(complete)
            self.assertTrue(result["changed"])
            self.assertEqual(load_snapshot(result["path"])["manifest"]["status"], "success")
            self.assertEqual(load_snapshot(result["history_path"])["manifest"]["status"], "partial")


class ComparatorTests(SimpleTestCase):
    def changed_pair(self):
        left = snapshot("sandbox")
        right = copy.deepcopy(left)
        right["manifest"]["profile"] = "production"
        right["manifest"]["environment"] = "production"
        return left, right

    def test_detects_modules_fields_relationship_layout_and_picklist_changes(self):
        left, right = self.changed_pair()
        right["modules"].append({"api_name": "Added", "module_name": "Added", "label": "Added"})
        right["modules"] = [item for item in right["modules"] if item["api_name"] != "Restricted"]
        field = next(item for item in right["fields"] if item["api_name"] == "Tomador")
        field["data_type"] = "text"
        field["required"] = False
        field["lookup"] = {"module": {"api_name": "Accounts"}}
        right["fields"].append({"module_api_name": "Polizas", "api_name": "New_Field", "data_type": "text"})
        right["layouts"][0]["visible"] = False
        right["relationships"][0]["target_module_api_name"] = "Accounts"
        right["picklists"][0]["active"] = False
        result = compare_snapshots(left, right)
        categories = {item["change"] for item in result["fields"]}
        self.assertIn("field_added", categories)
        self.assertIn("field_type_changed", categories)
        self.assertIn("field_required_changed", categories)
        self.assertIn("field_lookup_changed", categories)
        self.assertEqual(result["summary"]["modules_added"], 1)
        self.assertEqual(result["summary"]["modules_removed"], 1)
        self.assertEqual(result["summary"]["layouts_changed"], 1)
        self.assertEqual(result["summary"]["relationships_changed"], 1)
        self.assertIn("value_disabled", {item["change"] for item in result["picklists"]})
        self.assertIn("field_picklist_changed", {item["change"] for item in result["fields"]})
        self.assertIn(
            "relationship_target_changed",
            {item["change"] for item in result["relationships"]["events"]},
        )
        self.assertIn("layout_changed", {item["change"] for item in result["layouts"]["events"]})

    def test_field_relationship_and_picklist_removal_are_detected(self):
        left, right = self.changed_pair()
        right["fields"] = [item for item in right["fields"] if item["api_name"] != "Tomador"]
        right["relationships"] = [item for item in right["relationships"] if item["source_field_api_name"] != "Tomador"]
        right["picklists"] = []
        result = compare_snapshots(left, right)
        self.assertIn("field_removed", {item["change"] for item in result["fields"]})
        self.assertEqual(result["summary"]["relationships_removed"], 1)
        self.assertIn("value_removed", {item["change"] for item in result["picklists"]})
        self.assertIn(
            "relationship_removed",
            {item["change"] for item in result["relationships"]["events"]},
        )

    def test_equal_snapshots_produce_empty_diff_and_human_outputs(self):
        value = snapshot()
        result = compare_snapshots(value, copy.deepcopy(value))
        self.assertTrue(result["identical"])
        self.assertFalse(result["critical_changes"])
        markdown = render_comparison_markdown(result)
        self.assertIn("semánticamente iguales", markdown)
        model = render_model_markdown(value)
        self.assertIn("Estado del snapshot: **PARTIAL**", model)
        self.assertIn("pendiente de confirmación funcional", model)
        self.assertIn("No se asumió", model)

    def test_unavailable_fields_make_comparison_inconclusive_not_removed_or_added(self):
        left, right = self.changed_pair()
        module = next(item for item in left["modules"] if item["api_name"] == "Polizas")
        module["fields_status"] = "error"
        module["fields_metadata_status"] = "unavailable"
        left["fields"] = [item for item in left["fields"] if item["module_api_name"] != "Polizas"]
        left["relationships"] = [
            item for item in left["relationships"] if item["source_module"] != "Polizas"
        ]
        result = compare_snapshots(left, right)
        polizas = [item for item in result["fields"] if item["identity"][0] == "Polizas"]
        self.assertEqual({item["change"] for item in polizas}, {"comparison_inconclusive"})
        self.assertEqual(polizas[0]["reason"], "metadata_unavailable_left")
        self.assertNotIn("field_added", {item["change"] for item in polizas})
        reverse = compare_snapshots(right, left)
        self.assertNotIn(
            "field_removed",
            {item["change"] for item in reverse["fields"] if item["identity"][0] == "Polizas"},
        )
        self.assertIn("No comparable", render_comparison_markdown(result))


class DiscoverySecurityAndCommandTests(SimpleTestCase):
    def test_recursive_sanitizer_removes_all_secret_categories(self):
        cleaned = safe_value({
            "access_token": "access-secret", "nested": {
                "refresh-token": "refresh-secret", "client_secret": "client-secret",
                "password": "password-secret", "safe": "metadata",
            },
        })
        serialized = json.dumps(cleaned)
        for secret in ("access-secret", "refresh-secret", "client-secret", "password-secret"):
            self.assertNotIn(secret, serialized)
        self.assertIn("metadata", serialized)
        self.assertIsNone(safe_value(SimpleNamespace(secret="must-not-render")))

    def test_snapshot_and_logs_never_contain_secrets_or_card_data(self):
        value = snapshot()
        serialized = json.dumps(value)
        for forbidden in (
            "access-secret", "refresh-secret", "client-secret", "4111111111111111", "cvv",
        ):
            self.assertNotIn(forbidden, serialized.casefold())
        with self.assertLogs("integrations.zoho", level="INFO") as captured:
            DiscoveryService(profile="sandbox", facade=fake_facade()).discover()
        output = " ".join(captured.output).casefold()
        self.assertNotIn("token", output)
        self.assertNotIn("organization_id", output)

    @patch("integrations.management.commands.zoho_discover.DiscoveryService")
    def test_discover_command_requires_explicit_profile_and_writes_safe_summary(self, service):
        service.return_value.discover.return_value = snapshot()
        with TemporaryDirectory() as directory:
            output = StringIO()
            call_command("zoho_discover", profile="sandbox", output_dir=directory, stdout=output)
            self.assertIn("Profile: sandbox", output.getvalue())
            self.assertIn("Status: partial", output.getvalue())
            self.assertIn("completed with warnings", output.getvalue())
            self.assertIn("No records", output.getvalue())
        with self.assertRaises((CommandError, TypeError)):
            call_command("zoho_discover", stdout=StringIO())

    @patch("integrations.management.commands.zoho_compare.load_snapshot")
    def test_compare_command_is_local_and_never_initializes_zoho(self, loader):
        loader.side_effect = (snapshot("sandbox"), snapshot("production"))
        with TemporaryDirectory() as directory, patch(
            "integrations.zoho.discovery.service.get_zoho"
        ) as get_zoho:
            output = StringIO()
            call_command(
                "zoho_compare", left="sandbox", right="production",
                root=directory, stdout=output,
            )
            get_zoho.assert_not_called()
            self.assertTrue((Path(directory) / "comparison" / "sandbox_vs_production.json").exists())
            self.assertTrue((Path(directory) / "MODEL.md").exists())
            self.assertIn("No Zoho facade", output.getvalue())

    @patch("integrations.management.commands.zoho_discover.DiscoveryService")
    def test_fatal_discovery_does_not_touch_existing_latest(self, service):
        for label, message in (
            ("organización", "No fue posible obtener la organización Zoho."),
            ("módulos", "No fue posible obtener los módulos Zoho."),
        ):
            with self.subTest(label=label), TemporaryDirectory() as directory:
                service.return_value.discover.side_effect = DiscoveryFatalError(message)
                latest = Path(directory) / "sandbox" / "latest"
                latest.mkdir(parents=True)
                marker = latest / ".gitkeep"
                marker.write_bytes(b"keep-me")
                with self.assertRaisesMessage(CommandError, label):
                    call_command(
                        "zoho_discover", profile="sandbox", output_dir=directory,
                        stdout=StringIO(), stderr=StringIO(),
                    )
                self.assertEqual(marker.read_bytes(), b"keep-me")
                self.assertFalse((Path(directory) / "sandbox" / "history").exists())
