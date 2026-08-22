from __future__ import annotations

from datetime import timedelta
from uuid import uuid4
from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from cotizacion_colectivos.dto import GroupMember, PolicyDetail
from cotizacion_colectivos.excel import build_current_policy_workbook
from cotizacion_colectivos.models import WorkspacePolizaColectivo
from cotizacion_colectivos.services.common import sign_record_id
from cotizacion_colectivos.services.common import ColectivosServiceError
from cotizacion_colectivos.services.functional_groups import consolidate_functional_groups
from cotizacion_colectivos.services.policies import PolicyService
from cotizacion_colectivos.services.preparations import (
    _identity,
    invalidate_policy_preparation,
    load_policy_preparation,
    store_policy_preparation,
)


POLICY_TOKEN = sign_record_id(
    "4234567890123456789", "policy",
    context={"source_id": "5234567890123456789", "source_kind": "company"},
)


def policy_detail():
    return PolicyDetail(
        detail_token=POLICY_TOKEN,
        masked_reference="Referencia terminada en 6789",
        branch_code="91",
        branch_name="Salud colectivo",
        classification="confirmed",
        insurer="Aseguradora",
        state="Vigente",
        holder="Información protegida",
        start_date="2026-01-01",
        end_date="2026-12-31",
        renewable="Sí",
        payment_mode="Mensual",
        frequency="Mensual",
        installments="12",
        first_installment_date="2026-01-01",
        payment_calendar=(),
        insured=(),
        risks=(),
        active_count=1,
        excluded_count=0,
    )


def member():
    return GroupMember(
        role="Asegurado", display_name="Información protegida", id_type="CC",
        masked_document="•••123", state="Activo", entry_date="",
        exit_date="", plan="Plan", relationship="Titular", risk_summary="",
        insured_key="a" * 64,
    )


@override_settings(
    ZOHO_ACTIVE_PROFILE="sandbox",
    ZOHO_BACKEND="sdk",
    COLECTIVOS_POLICY_PREPARATION_TTL_SECONDS=600,
    COLECTIVOS_POLICY_WORKSPACE_TTL_SECONDS=600,
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
)
class PolicyPreparationTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_encrypted_preparation_round_trip_and_lazy_facade_cache_hit(self):
        store_policy_preparation(
            token=POLICY_TOKEN, profile="sandbox", backend="sdk",
            source_kind="company", detail=policy_detail(), members=(member(),),
        )
        with patch("cotizacion_colectivos.services.policies.colectivos_zoho") as facade:
            detail, members = PolicyService().group(POLICY_TOKEN, source_kind="company")
        facade.assert_not_called()
        self.assertEqual(detail.branch_code, "91")
        self.assertEqual(len(members), 1)

    def test_restored_workspace_uses_current_capability_and_sanitizes_legacy_layout(self):
        stored = policy_detail()
        stored = PolicyDetail(**{
            **stored.__dict__,
            "detail_token": "expired-capability",
            "layout_name": "<sdk.layouts.Layout object at 0x1234>",
        })
        store_policy_preparation(
            token=POLICY_TOKEN, profile="sandbox", backend="sdk",
            source_kind="company", detail=stored, members=(member(),),
        )
        current_token = sign_record_id(
            "4234567890123456789", "policy",
            context={"source_id": "5234567890123456789", "source_kind": "company"},
        )

        detail, _members, _metadata = load_policy_preparation(
            token=current_token, profile="sandbox", backend="sdk",
            source_kind="company",
        )

        self.assertEqual(detail.detail_token, current_token)
        self.assertEqual(detail.layout_name, "")
        self.assertNotIn("sdk", repr(detail))

    def test_database_workspace_survives_cache_loss_without_remote_call(self):
        store_policy_preparation(
            token=POLICY_TOKEN, profile="sandbox", backend="sdk",
            source_kind="company", detail=policy_detail(), members=(member(),),
            timings={"remote_queries": 4, "policy_lookup_ms": 20},
        )
        workspace = WorkspacePolizaColectivo.objects.get()
        self.assertNotIn("Referencia terminada", workspace.encrypted_snapshot)
        self.assertEqual(workspace.record_count, 1)
        cache.clear()
        with patch("cotizacion_colectivos.services.policies.colectivos_zoho") as facade:
            service = PolicyService()
            detail, members = service.group(POLICY_TOKEN, source_kind="company")
        facade.assert_not_called()
        self.assertEqual(service.preparation_metadata["storage"], "database")
        self.assertEqual(service.timings["remote_queries"], 0)
        self.assertEqual(detail.branch_code, "91")
        self.assertEqual(len(members), 1)

    def test_workspace_contains_precomputed_functional_group_and_safe_timeline(self):
        metadata = store_policy_preparation(
            token=POLICY_TOKEN, profile="sandbox", backend="sdk",
            source_kind="company", detail=policy_detail(), members=(member(),),
            timings={"remote_queries": 4},
        )
        self.assertEqual(len(metadata["functional_groups"]), 1)
        cache.clear()
        loaded = load_policy_preparation(
            token=POLICY_TOKEN, profile="sandbox", backend="sdk",
            source_kind="company",
        )
        self.assertEqual(len(loaded[2]["functional_groups"]), 1)
        self.assertEqual(loaded[2]["safe_timeline"][0]["remote_queries"], 4)

    def test_excel_is_generated_from_persistent_workspace_without_zoho(self):
        store_policy_preparation(
            token=POLICY_TOKEN, profile="sandbox", backend="sdk",
            source_kind="company", detail=policy_detail(), members=(member(),),
        )
        cache.clear()
        with patch("cotizacion_colectivos.services.policies.colectivos_zoho") as facade:
            content = build_current_policy_workbook(POLICY_TOKEN)
        facade.assert_not_called()
        self.assertTrue(content.startswith(b"PK"))

    def test_manual_refresh_atomically_replaces_snapshot_and_increments_revision(self):
        store_policy_preparation(
            token=POLICY_TOKEN, profile="sandbox", backend="sdk",
            source_kind="company", detail=policy_detail(), members=(member(),),
        )
        refreshed = store_policy_preparation(
            token=POLICY_TOKEN, profile="sandbox", backend="sdk",
            source_kind="company", detail=policy_detail(), members=(member(),),
            actor="manual_refresh", timings={"remote_queries": 4},
        )
        workspace = WorkspacePolizaColectivo.objects.get()
        self.assertEqual(refreshed["revision"], 2)
        self.assertEqual(workspace.revision, 2)
        self.assertEqual([event["type"] for event in workspace.safe_timeline], [
            "ZOHO_INITIAL_LOAD", "ZOHO_REFRESH",
        ])

    def test_refresh_bypass_does_not_destroy_last_valid_workspace(self):
        store_policy_preparation(
            token=POLICY_TOKEN, profile="sandbox", backend="sdk",
            source_kind="company", detail=policy_detail(), members=(member(),),
        )
        invalidate_policy_preparation(
            token=POLICY_TOKEN, profile="sandbox", backend="sdk",
            source_kind="company",
        )
        restored = load_policy_preparation(
            token=POLICY_TOKEN, profile="sandbox", backend="sdk",
            source_kind="company",
        )
        self.assertIsNotNone(restored)
        self.assertEqual(restored[2]["storage"], "database")

    def test_invalid_persistent_workspace_fails_closed_without_zoho(self):
        store_policy_preparation(
            token=POLICY_TOKEN, profile="sandbox", backend="sdk",
            source_kind="company", detail=policy_detail(), members=(member(),),
        )
        WorkspacePolizaColectivo.objects.update(snapshot_checksum="0" * 64)
        cache.clear()
        with patch("cotizacion_colectivos.services.policies.colectivos_zoho") as facade:
            with self.assertRaises(ColectivosServiceError) as raised:
                PolicyService().group(POLICY_TOKEN, source_kind="company")
        facade.assert_not_called()
        self.assertEqual(raised.exception.code, "invalid_response")

    def test_wrong_profile_and_tampered_ciphertext_are_rejected(self):
        store_policy_preparation(
            token=POLICY_TOKEN, profile="sandbox", backend="sdk",
            source_kind="company", detail=policy_detail(), members=(member(),),
        )
        self.assertIsNone(load_policy_preparation(
            token=POLICY_TOKEN, profile="production", backend="sdk", source_kind="company",
        ))
        key, _context = _identity(
            token=POLICY_TOKEN, profile="sandbox", backend="sdk", source_kind="company",
        )
        cache.set(key, "ciphertext-alterado", 600)
        status = {}
        self.assertIsNone(load_policy_preparation(
            token=POLICY_TOKEN, profile="sandbox", backend="sdk",
            source_kind="company", status_out=status,
        ))
        self.assertEqual(status["status"], "invalid")

    def test_expired_preparation_is_not_reused(self):
        queried = timezone.now()
        store_policy_preparation(
            token=POLICY_TOKEN, profile="sandbox", backend="sdk",
            source_kind="company", detail=policy_detail(), members=(member(),),
        )
        status = {}
        with patch(
            "cotizacion_colectivos.services.preparations.timezone.now",
            return_value=queried + timedelta(seconds=601),
        ):
            result = load_policy_preparation(
                token=POLICY_TOKEN, profile="sandbox", backend="sdk",
                source_kind="company", status_out=status,
            )
        self.assertIsNone(result)
        self.assertEqual(status["status"], "expired")


class FunctionalGroupingTests(SimpleTestCase):
    @staticmethod
    def row(*, role, key, public_key=None, display="Persona protegida", risk_key=""):
        prefix = {"Afiliado": "associate", "Asegurado": "insured", "Beneficiario": "beneficiary"}[role]
        return {
            "public_key": public_key or uuid4(), "role": role,
            f"{prefix}_key": key, f"{prefix}_name": display,
            "initial_status": "Activo", "plan": "Plan", "relationship": "",
            "risk_key": risk_key, "risk_summary": "Riesgo protegido",
            "risk_attributes": {},
        }

    def test_same_reference_with_multiple_roles_is_rendered_once(self):
        key = "a" * 64
        groups, warnings = consolidate_functional_groups((
            self.row(role="Afiliado", key=key),
            self.row(role="Asegurado", key=key),
        ), branch_code="91")
        entities = [group["principal"] for group in groups]
        self.assertEqual(len(entities), 1)
        self.assertEqual(set(entities[0]["roles"]), {"Afiliado", "Asegurado"})
        self.assertEqual(len(entities[0]["source_record_keys"]), 2)
        self.assertEqual(warnings, ())

    def test_equal_names_with_different_references_are_never_merged(self):
        groups, _warnings = consolidate_functional_groups((
            self.row(role="Asegurado", key="a" * 64, display="Mismo texto"),
            self.row(role="Asegurado", key="b" * 64, display="Mismo texto"),
        ), branch_code="91")
        self.assertEqual(len(groups), 2)

    def test_beneficiary_without_confirmed_principal_creates_internal_warning(self):
        groups, warnings = consolidate_functional_groups((
            self.row(role="Beneficiario", key="c" * 64),
        ), branch_code="86")
        self.assertEqual(len(groups), 1)
        self.assertTrue(any("beneficiario" in warning.casefold() for warning in warnings))

    def test_connected_roles_do_not_repeat_a_real_person_across_groups(self):
        first = self.row(role="Afiliado", key="a" * 64, display="Principal")
        first.update({
            "beneficiary_key": "b" * 64,
            "beneficiary_name": "Dependiente",
            "beneficiary_id_type": "CC",
            "beneficiary_masked_document": "•••002",
        })
        second = self.row(role="Afiliado", key="b" * 64, display="Dependiente")
        second.update({
            "beneficiary_key": "c" * 64,
            "beneficiary_name": "Beneficiario",
            "beneficiary_id_type": "CC",
            "beneficiary_masked_document": "•••003",
        })
        groups, _warnings = consolidate_functional_groups((first, second), branch_code="91")
        rendered_keys = []
        for group in groups:
            rendered_keys.append(group["principal"]["key"])
            rendered_keys.extend(member["key"] for member in group["members"])
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(rendered_keys), 3)
        self.assertEqual(len(set(rendered_keys)), 3)

    def test_home_and_mobility_are_grouped_by_risk_reference(self):
        for branch, label in (("28", "inmueble"), ("40", "vehículos")):
            groups, _warnings = consolidate_functional_groups((
                self.row(role="Asegurado", key="a" * 64, risk_key="d" * 64),
            ), branch_code=branch)
            self.assertEqual(len(groups), 1)
            self.assertIn(label, groups[0]["action_label"].casefold())
