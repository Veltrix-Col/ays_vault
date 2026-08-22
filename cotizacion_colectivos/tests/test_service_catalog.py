from django.test import SimpleTestCase
from django.urls import reverse

from cotizacion_colectivos.dto import BranchSummary, RelatedPolicy
from cotizacion_colectivos.service_catalog import branch_workspaces, services_for_branch


class CollectiveServiceCatalogTests(SimpleTestCase):
    def test_services_are_derived_from_confirmed_branch_capabilities(self):
        self.assertEqual(
            {service.code for service in services_for_branch("40")},
            {"novelties", "individual", "invitations"},
        )
        self.assertEqual(
            {service.code for service in services_for_branch("91")},
            {"novelties", "individual"},
        )

    def test_workspace_uses_full_policy_number_and_server_allowlisted_routes(self):
        policy = RelatedPolicy(
            detail_token="opaque-token",
            masked_reference="Póliza terminada en 6789",
            full_reference="04000123456789",
            state="Vigente",
            branch="Movilidad colectivo",
            insurer="SURA",
        )
        branch = BranchSummary(
            code="40", slug="movilidad-colectivo", name="Movilidad colectivo",
            classification="confirmed", policies=(policy,), insured_count=1,
            risk_count=1, active_count=1, excluded_count=0,
        )
        workspace = branch_workspaces((branch,))[0]
        self.assertTrue(all(entry["policies"][0]["reference"] == "04000123456789" for entry in workspace["services"]))
        self.assertEqual(
            workspace["services"][0]["policies"][0]["url"],
            reverse("cotizacion_colectivos:novelties_policy_detail", args=["opaque-token"]),
        )

    def test_contextual_workspace_never_falls_back_to_another_tool(self):
        policy = RelatedPolicy(
            detail_token="opaque-token", masked_reference="Póliza terminada en 6789",
            full_reference="04000123456789", state="Vigente",
            branch="Movilidad colectivo", insurer="SURA",
        )
        branch = BranchSummary(
            code="40", slug="movilidad-colectivo", name="Movilidad colectivo",
            classification="confirmed", policies=(policy,), insured_count=1,
            risk_count=1, active_count=1, excluded_count=0,
        )
        for selected in ("individual", "invitations", "novelties"):
            with self.subTest(selected=selected):
                workspace = branch_workspaces((branch,), service_code=selected)[0]
                self.assertEqual(
                    tuple(entry["service"].code for entry in workspace["services"]),
                    (selected,),
                )
