from __future__ import annotations

import re
from dataclasses import replace
from datetime import timedelta
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import urlsplit

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import Client, TestCase, override_settings
from django.urls import resolve, reverse
from django.utils import timezone

from cotizacion_colectivos.dto import BranchSummary, CompanyDetail, ContactSummary, GroupMember, PolicyDetail, RelatedPolicy
from cotizacion_colectivos.excel import build_current_policy_workbook
from cotizacion_colectivos.models import (
    AccesoExternoSolicitudColectivo,
    SolicitudColectivo,
    SolicitudColectivoPoliza,
    SolicitudColectivoRegistro,
)
from cotizacion_colectivos.services.common import ColectivosServiceError, sign_record_id, unsign_record_context
from cotizacion_colectivos.services.external import generate_access, resolve_token
from cotizacion_colectivos.services.external import ExternalAccessError
from cotizacion_colectivos.services.requests import (
    create_request_from_policy,
    request_reference_hashes,
)


POLICY_ID = "4234567890123456789"
SOURCE_ID = "5234567890123456789"
TOKEN = sign_record_id(
    POLICY_ID,
    "policy",
    context={"source_id": SOURCE_ID, "source_kind": "company"},
)
PERSON_TOKEN = sign_record_id(
    POLICY_ID,
    "policy",
    context={"source_id": SOURCE_ID, "source_kind": "person"},
)


class AnchorByTextParser(HTMLParser):
    def __init__(self, expected_text):
        super().__init__(convert_charrefs=True)
        self.expected_text = expected_text
        self.href = None
        self._current_href = None
        self._current_text = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._current_href = dict(attrs).get("href")
            self._current_text = []

    def handle_data(self, data):
        if self._current_href is not None:
            self._current_text.append(data)

    def handle_endtag(self, tag):
        if tag != "a" or self._current_href is None:
            return
        if "".join(self._current_text).strip() == self.expected_text:
            self.href = self._current_href
        self._current_href = None
        self._current_text = []


def anchor_href(response, text):
    parser = AnchorByTextParser(text)
    parser.feed(response.content.decode(response.charset or "utf-8"))
    return parser.href


def _policy(**overrides):
    values = {
        "detail_token": TOKEN,
        "masked_reference": "Referencia terminada en 3456",
        "branch_code": "91",
        "branch_name": "Salud colectivo",
        "classification": "confirmed",
        "insurer": "Aseguradora",
        "state": "Vigente",
        "holder": "Empresa autorizada",
        "start_date": "2026-01-01",
        "end_date": "2026-12-31",
        "renewable": "Sí",
        "payment_mode": "Fraccionado",
        "frequency": "Mensual",
        "installments": "12",
        "first_installment_date": "2026-01-01",
        "payment_calendar": (),
        "insured": (),
        "risks": (),
        "active_count": 1,
        "excluded_count": 0,
        "warnings": (),
    }
    values.update(overrides)
    return PolicyDetail(**values)


MEMBER = GroupMember(
    role="Asegurado",
    display_name="Persona interna",
    id_type="CC",
    masked_document="••••890",
    state="Activo",
    entry_date="2026-01-01",
    exit_date="",
    plan="Plan A",
    relationship="Titular",
    risk_summary="",
    insured_name="Persona interna",
    insured_id_type="CC",
    insured_document="100000890",
    insured_masked_document="••••890",
    insured_key="insured-hmac-key",
)


class FakePolicyService:
    profile = "sandbox"
    preparation_status = "hit"
    timings = {"remote_queries": 0}

    def __init__(self, detail=None):
        self.policy = detail or _policy()

    def detail(self, token):
        if token != TOKEN:
            raise ColectivosServiceError("not_found", "No encontrada")
        return self.policy

    def group(self, token, *, source_kind=None):
        return self.detail(token), (MEMBER,)

    def _relations(self, policy_id):
        return (), False

    def _batch(self, module, fields, ids):
        return {}


class AnyPolicyTokenService(FakePolicyService):
    def detail(self, token):
        unsign_record_context(token, "policy")
        return self.policy


class FakeEntityDetailService:
    def company(self, token):
        unsign_record_context(token, "company")
        policy = RelatedPolicy(
            detail_token=TOKEN,
            masked_reference="Referencia terminada en 3456",
            state="Vigente",
            branch="Salud colectivo",
            insurer="Aseguradora",
            layout_name="Colectivos",
            layout_category="collective",
            renewable="Sí",
        )
        branch = BranchSummary(
            code="91", slug="salud", name="Salud colectivo", classification="confirmed",
            policies=(policy,), insured_count=1, risk_count=0, active_count=1,
            excluded_count=0,
        )
        return CompanyDetail(
            display_name="Empresa autorizada", legal_name="Empresa autorizada",
            id_type="NIT", masked_document="•••789", state="Cliente",
            summary=ContactSummary("Persona jurídica", "NIT", "•••789", "Cliente", document="900123789"),
            policies=(policy,), direct_policies=(), insured=(), risks=(), branches=(branch,),
            document="900123789",
        )


@override_settings(
    ZOHO_ACTIVE_PROFILE="sandbox",
    COLECTIVOS_INTERNAL_PUBLIC_ACCESS=False,
    COLECTIVOS_EXTERNAL_BASE_URL="https://colectivos.example.test",
    COLECTIVOS_EXTERNAL_LINK_TTL_SECONDS=3600,
    COLECTIVOS_EXTERNAL_LINK_MAX_TTL_SECONDS=7200,
)
class PolicyNavigationTests(TestCase):
    def test_individual_otp_toggle_is_opt_in_and_email_field_is_conditional(self):
        template = (Path(__file__).resolve().parents[2] / "templates" / "cotizacion_colectivos" / "policy_detail.html").read_text(encoding="utf-8")
        self.assertIn('name="otp_required"', template)
        self.assertIn("Solicitar código de verificación por correo", template)
        self.assertIn("data-individual-otp-fields", template)
        self.assertIn("data-individual-otp-toggle", template)
        self.assertIn("data-individual-otp-fields{% if not individual_otp_required %} hidden{% endif %}", template)
        self.assertIn("recipient.required = enabled", (Path(__file__).resolve().parents[2] / "static" / "js" / "colectivos-access.js").read_text(encoding="utf-8"))
        self.assertIn(".individual-otp-toggle input[type=checkbox]", (Path(__file__).resolve().parents[2] / "static" / "css" / "colectivos.css").read_text(encoding="utf-8"))
        self.assertIn(".individual-access-field[hidden]{display:none!important}", (Path(__file__).resolve().parents[2] / "static" / "css" / "colectivos.css").read_text(encoding="utf-8"))
        self.assertIn('toggle.setAttribute("aria-expanded"', (Path(__file__).resolve().parents[2] / "static" / "js" / "colectivos-access.js").read_text(encoding="utf-8"))

    def setUp(self):
        cache.clear()
        User = get_user_model()
        self.admin = User.objects.create_superuser(
            "navigation-admin", "navigation@example.test", "Password123!"
        )
        self.user = User.objects.create_user("navigation-user", password="Password123!")
        self.client.force_login(self.admin)

    def create_request(self, request_type=SolicitudColectivo.RequestType.UPDATE):
        return create_request_from_policy(
            token=TOKEN,
            source_kind="company",
            actor=self.admin,
            assigned_to=self.admin,
            request_type=request_type,
            deadline=timezone.localdate() + timedelta(days=10),
            internal_notes="",
            service=FakePolicyService(),
        )

    def policy_page(self, service=None, client=None, token=TOKEN):
        with patch(
            "cotizacion_colectivos.views.PolicyService",
            return_value=service or FakePolicyService(),
        ):
            return (client or self.client).get(
                reverse("cotizacion_colectivos:policy_detail", args=[token])
            )

    def test_internal_client_detail_shows_complete_document(self):
        company_token = sign_record_id(SOURCE_ID, "company")
        with patch(
            "cotizacion_colectivos.views.EntityDetailService",
            return_value=FakeEntityDetailService(),
        ):
            response = self.client.get(reverse(
                "cotizacion_colectivos:client_detail",
                args=["company", company_token],
            ))
        self.assertContains(response, "900123789")
        self.assertNotContains(response, "NIT •••789")

    def test_home_only_displays_active_zoho_profile_without_runtime_switch(self):
        response = self.client.get(reverse("cotizacion_colectivos:invitations_index"))
        self.assertContains(response, "Perfil Zoho activo: SANDBOX")
        self.assertContains(response, "ZOHO_ACTIVE_PROFILE")
        self.assertNotContains(response, 'name="zoho_profile"', html=False)

    def test_branch_page_consolidates_only_active_policies_from_same_branch(self):
        from cotizacion_colectivos.invitation_templates.catalog import templates_for_branch
        from cotizacion_colectivos.services.invitation_templates import TemplatePreview
        company_token = sign_record_id(SOURCE_ID, "company")
        base_detail = FakeEntityDetailService().company(company_token)
        active = replace(
            base_detail.branches[0].policies[0],
            full_reference="23696696", branch="Movilidad colectivo",
        )
        inactive = replace(
            active, detail_token=sign_record_id(
                "4234567890123456790", "policy",
                context={"source_id": SOURCE_ID, "source_kind": "company"},
            ), full_reference="INACTIVA-900", state="Vencida",
        )
        other = replace(
            active, detail_token=sign_record_id(
                "4234567890123456791", "policy",
                context={"source_id": SOURCE_ID, "source_kind": "company"},
            ), full_reference="OTRO-RAMO", branch="Salud colectivo",
        )
        mobility = BranchSummary(
            code="40", slug="movilidad", name="Movilidad colectivo",
            classification="confirmed", policies=(active, inactive),
            insured_count=2, risk_count=2, active_count=1, excluded_count=1,
        )
        health = replace(base_detail.branches[0], policies=(other,))
        detail = replace(
            base_detail, policies=(active, inactive, other), branches=(mobility, health),
        )
        service = Mock()
        service.company.return_value = detail
        previews = tuple(
            TemplatePreview(item, "ready", 1, 0, 1, 100, ())
            for item in templates_for_branch("40", active_only=True)
        )
        invitation_detail = _policy(
            detail_token=active.detail_token, branch_code="40",
            branch_name="Movilidad colectivo", full_reference="23696696",
        )
        metadata = {
            "complete": True, "remote_queries": 0,
            "operational_groups": ({
                "policy_token": active.detail_token,
                "policy_reference": "23696696", "insurer": active.insurer,
                "rows": ({"document": "100000890", "insured_name": "Persona interna",
                          "plate": "ABC123", "model": "2025", "brand": "Marca",
                          "city": "Bogotá", "relationship": "Titular"},),
            },),
        }
        with patch("cotizacion_colectivos.views.EntityDetailService", return_value=service), patch(
            "cotizacion_colectivos.views.preview_invitation_templates",
            return_value=(invitation_detail, previews, metadata),
        ):
            response = self.client.get(reverse(
                "cotizacion_colectivos:invitations_branch_detail",
                args=["company", company_token, "40"],
            ))
        self.assertContains(response, "23696696")
        self.assertContains(response, "Información consolidada del ramo")
        self.assertContains(response, "ABC123")
        self.assertContains(response, "Limpiar selección")
        self.assertContains(response, "Ver póliza")
        self.assertContains(response, "Descargar formato SURA")
        self.assertContains(response, "Descargar formato Allianz")
        self.assertContains(response, "data-invitation-policy-filter", html=False)
        self.assertNotContains(response, "Invitar aseguradoras con todo el ramo")
        self.assertNotContains(response, "INACTIVA-900")
        self.assertNotContains(response, "OTRO-RAMO")

    def test_branch_page_does_not_abort_when_every_workspace_is_pending(self):
        company_token = sign_record_id(SOURCE_ID, "company")
        service = Mock()
        service.company.return_value = FakeEntityDetailService().company(company_token)
        with patch("cotizacion_colectivos.views.EntityDetailService", return_value=service), patch(
            "cotizacion_colectivos.views.preview_invitation_templates",
            side_effect=ColectivosServiceError("workspace_unavailable", "Pendiente"),
        ):
            response = self.client.get(reverse(
                "cotizacion_colectivos:invitations_branch_detail",
                args=["company", company_token, "91"],
            ))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Información pendiente de actualizar")
        self.assertContains(response, "Las pólizas con información local disponible continúan operativas")
        self.assertNotContains(response, "No fue posible cargar la ficha")

    def test_expedient_links_to_signed_client_policy_and_insured_context(self):
        item = self.create_request()
        response = self.client.get(reverse(
            "cotizacion_colectivos:request_detail", args=[item.public_id],
        ))
        self.assertContains(response, "Empresa autorizada")
        self.assertContains(response, "Abrir personas y riesgos de la póliza")
        self.assertContains(response, reverse(
            "cotizacion_colectivos:policy_detail", args=[TOKEN],
        ))
        self.assertContains(response, reverse(
            "cotizacion_colectivos:policy_group", args=[TOKEN],
        ))
        self.assertNotContains(response, SOURCE_ID)

    def test_company_breadcrumb_and_back_action_resolve_to_signed_source(self):
        service = FakePolicyService(_policy(source_name="Fonconstruimos", source_kind="company"))
        service.group = Mock(wraps=service.group)
        with patch("cotizacion_colectivos.views.EntityDetailService") as entity_service:
            response = self.policy_page(service)
        self.assertContains(response, "Novedades")
        self.assertContains(response, "Fonconstruimos")
        self.assertContains(response, "← Volver a la ficha del cliente")
        href = anchor_href(response, "← Volver a la ficha del cliente")
        match = resolve(urlsplit(href).path)
        self.assertEqual(match.view_name, "cotizacion_colectivos:novelties_client_detail")
        self.assertEqual(match.kwargs["entity_kind"], "company")
        source_context = unsign_record_context(match.kwargs["token"], "company")
        self.assertEqual(source_context, {"id": SOURCE_ID, "type": "company"})
        self.assertNotContains(response, SOURCE_ID)
        service.group.assert_called_once_with(TOKEN, source_kind="company")
        entity_service.assert_not_called()

    def test_person_breadcrumb_and_back_action_resolve_to_signed_source(self):
        detail = _policy(source_name="Persona de prueba", source_kind="person")
        response = self.policy_page(AnyPolicyTokenService(detail), token=PERSON_TOKEN)
        self.assertContains(response, "Novedades")
        self.assertContains(response, "Persona de prueba")
        href = anchor_href(response, "← Volver a la ficha del cliente")
        match = resolve(urlsplit(href).path)
        self.assertEqual(match.view_name, "cotizacion_colectivos:novelties_client_detail")
        self.assertEqual(match.kwargs["entity_kind"], "person")
        source_context = unsign_record_context(match.kwargs["token"], "person")
        self.assertEqual(source_context, {"id": SOURCE_ID, "type": "person"})
        self.assertNotContains(response, SOURCE_ID)

    def test_multiple_policies_return_to_the_same_source_entity(self):
        service = AnyPolicyTokenService(_policy(source_name="Fonconstruimos"))
        back_contexts = []
        for policy_id in ("4234567890123456789", "4234567890123456790"):
            policy_token = sign_record_id(
                policy_id, "policy",
                context={"source_id": SOURCE_ID, "source_kind": "company"},
            )
            response = self.policy_page(service, token=policy_token)
            href = anchor_href(response, "← Volver a la ficha del cliente")
            match = resolve(urlsplit(href).path)
            back_contexts.append(unsign_record_context(match.kwargs["token"], "company"))
        self.assertEqual(back_contexts, [
            {"id": SOURCE_ID, "type": "company"},
            {"id": SOURCE_ID, "type": "company"},
        ])

    def test_policy_navigation_classes_include_mobile_safe_layout(self):
        response = self.policy_page()
        self.assertContains(response, 'class="breadcrumbs policy-breadcrumbs"', html=False)
        self.assertContains(response, 'class="policy-back-link"', html=False)
        css = (settings.BASE_DIR / "static" / "css" / "colectivos.css").read_text(encoding="utf-8")
        self.assertIn(".policy-breadcrumbs", css)
        self.assertIn(".policy-back-link", css)
        self.assertIn("@media (max-width: 380px)", css)

    def test_policy_exposes_complete_navigation_without_obsolete_placeholders(self):
        response = self.policy_page()
        self.assertContains(response, "Ver grupo")
        self.assertContains(response, "Descargar Excel actual")
        self.assertContains(response, "Generar enlace")
        self.assertContains(response, "ingreso o un retiro")
        self.assertNotContains(response, "Renovación")
        self.assertContains(response, "Actualizar información desde Zoho")
        self.assertNotContains(response, "Generar enlace de actualización")
        self.assertNotContains(response, "Crear solicitud multipóliza")
        self.assertNotContains(response, "Novedades y solicitudes")
        self.assertNotContains(response, "Preparar envío")
        self.assertContains(response, "method=\"post\"")

    def test_same_policy_workspace_changes_only_the_primary_tool_context(self):
        with patch("cotizacion_colectivos.views.PolicyService", return_value=FakePolicyService()):
            requests_page = self.client.get(reverse(
                "cotizacion_colectivos:novelties_policy_detail", args=[TOKEN]
            ))
            invitations_page = self.client.get(reverse(
                "cotizacion_colectivos:invitations_policy_detail", args=[TOKEN]
            ))
            individual_page = self.client.get(reverse(
                "cotizacion_colectivos:individual_policy_detail", args=[TOKEN]
            ))
        self.assertContains(requests_page, "Novedades")
        self.assertContains(requests_page, "Generar enlace")
        self.assertContains(invitations_page, "Invitaciones a Aseguradoras")
        self.assertContains(invitations_page, "Descargar plantillas de invitación")
        self.assertContains(individual_page, "Cotización individual")
        self.assertContains(individual_page, "Generar enlace")
        self.assertContains(requests_page, "Salud colectivo")
        self.assertContains(invitations_page, "Salud colectivo")
        self.assertContains(individual_page, "Salud colectivo")
        # El selector conserva su contrato de backend y ahora ofrece búsqueda
        # incremental sobre las opciones ya cargadas en la página.
        self.assertContains(individual_page, 'data-searchable-select', html=False)
        self.assertContains(individual_page, 'data-searchable-input', html=False)
        self.assertContains(individual_page, 'name="affiliate_key"', html=False)
        self.assertContains(individual_page, "Nuevo afiliado")
        self.assertContains(individual_page, 'data-responsible-picker', html=False)

        # Buscar cliente sigue disponible en su ubicación funcional, pero no
        # forma parte de la barra superior compartida.
        header = individual_page.content.decode(individual_page.charset or "utf-8").split("</header>", 1)[0]
        self.assertNotIn("Buscar cliente", header)
        self.assertRegex(header, r'class="[^"]*\bnotification-link\b[^"]*"')
        self.assertIn("Buzón", header)
        self.assertIn(f'href="{reverse("cotizacion_colectivos:request_list")}"', header)
        self.assertRegex(header, r"Buzón\s*<span>\d+</span>")
        self.assertIn("colectivos-tool-nav", header)

    def test_colectivos_header_exposes_canonical_tool_navigation(self):
        """The shared Colectivos shell links to existing tools only."""
        with patch("cotizacion_colectivos.views.PolicyService", return_value=FakePolicyService()):
            response = self.client.get(reverse("cotizacion_colectivos:individual_policy_detail", args=[TOKEN]))

        header = response.content.decode(response.charset or "utf-8").split("</header>", 1)[0]
        for route_name in (
            "cotizacion_colectivos:novelties_index",
            "cotizacion_colectivos:individual_index",
            "cotizacion_colectivos:invitations_index",
            "conciliacion:index",
            "cotizacion_colectivos:request_list",
        ):
            self.assertIn(f'href="{reverse(route_name)}"', header)
        self.assertIn("Novedades", header)
        self.assertIn("Cotización Individual", header)
        self.assertIn("Invitaciones", header)
        self.assertIn("Conciliador", header)
        self.assertIn("Buzón", header)
        self.assertIn('class="is-active" aria-current="page"', header)
        self.assertIn("colectivos-tool-nav", header)

    def test_colectivos_quick_navigation_has_responsive_styles(self):
        css = (Path(__file__).resolve().parents[2] / "static" / "css" / "colectivos.css").read_text(encoding="utf-8")
        self.assertIn(".colectivos-tool-nav", css)
        self.assertIn(".colectivos-tool-nav__links", css)
        self.assertIn("@media (max-width: 760px)", css)

    @patch("cotizacion_colectivos.views.resolve_task_responsible_email", return_value="responsable@example.test")
    @patch("cotizacion_colectivos.views.task_responsible_options", return_value=(Mock(actual_value="RESP-1", display_value="Responsable Demo"),))
    @patch("cotizacion_colectivos.views.PolicyService", return_value=FakePolicyService())
    def test_individual_link_is_generated_from_policy_and_hmac_affiliate_without_zoho_id(self, _service, _options, _email):
        response = self.client.post(reverse(
            "cotizacion_colectivos:policy_individual_access", args=[TOKEN],
        ), {"recipient": "cliente@example.test", "responsible": "RESP-1"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Enlace listo para compartir")
        self.assertContains(response, "/solicitudes/colectivos/externa/cotizacion-individual/")
        self.assertNotContains(response, POLICY_ID)
        self.assertNotContains(response, "100000890")

    @patch("cotizacion_colectivos.views.resolve_task_responsible_email", side_effect=ValidationError("No fue posible asociar el responsable seleccionado con un correo corporativo en Zoho."))
    @patch("cotizacion_colectivos.views.task_responsible_options", return_value=(Mock(actual_value="RESP-1", display_value="Responsable Demo"),))
    @patch("cotizacion_colectivos.views.PolicyService", return_value=FakePolicyService())
    def test_responsible_without_email_does_not_block_client_link(self, _service, _options, _email):
        response = self.client.post(
            reverse("cotizacion_colectivos:policy_individual_access", args=[TOKEN]),
            {"recipient": "cliente@example.test", "responsible": "RESP-1"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Enlace listo para compartir")
        self.assertContains(response, "El enlace puede generarse")

    @patch("cotizacion_colectivos.views.PolicyService", return_value=FakePolicyService())
    def test_legacy_single_policy_endpoint_remains_compatible_and_reuses_request(self, _service):
        url = reverse(
            "cotizacion_colectivos:policy_generate_access",
            args=[TOKEN, SolicitudColectivo.RequestType.UPDATE],
        )
        first = self.client.post(url, {"recipient": "cliente@example.test"})
        self.assertEqual(first.status_code, 200)
        self.assertContains(first, "Copiar enlace")
        self.assertContains(first, 'data-copy-target="generated-policy-url"', html=False)
        self.assertContains(first, "Detalle de póliza")
        self.assertEqual(SolicitudColectivo.objects.count(), 1)
        item = SolicitudColectivo.objects.get()
        self.assertEqual(item.status, item.Status.READY)
        self.assertEqual(item.external_accesses.count(), 1)
        event_count = item.events.count()
        notification_count = item.notifications.count()

        second = self.client.post(url, {"recipient": "cliente@example.test"})
        self.assertEqual(second.status_code, 200)
        self.assertContains(second, "Existe un enlace vigente")
        self.assertContains(second, "Generar nuevo enlace")
        self.assertEqual(SolicitudColectivo.objects.count(), 1)
        self.assertEqual(item.external_accesses.count(), 1)
        self.assertEqual(item.events.count(), event_count)
        self.assertEqual(item.notifications.count(), notification_count)
        self.assertEqual(
            item.external_accesses.filter(
                status=AccesoExternoSolicitudColectivo.Status.ACTIVE
            ).count(),
            1,
        )

        regenerated = self.client.post(url, {"force_new": "1", "recipient": "cliente@example.test"})
        self.assertEqual(regenerated.status_code, 200)
        self.assertContains(regenerated, "Copiar enlace")
        self.assertEqual(item.external_accesses.count(), 2)
        self.assertEqual(
            item.external_accesses.filter(
                status=AccesoExternoSolicitudColectivo.Status.REVOKED
            ).count(),
            1,
        )

    @patch("cotizacion_colectivos.views.PolicyService", return_value=FakePolicyService())
    def test_simple_flow_uses_defaults_without_preliminary_form(self, _service):
        response = self.client.post(
            reverse("cotizacion_colectivos:policy_generate_access_simple", args=[TOKEN]),
            {"request_type": SolicitudColectivo.RequestType.UPDATE, "recipient": "cliente@example.test"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Copiar enlace")
        item = SolicitudColectivo.objects.get()
        self.assertEqual(
            item.deadline,
            timezone.localdate() + timedelta(days=settings.COLECTIVOS_EXTERNAL_LINK_DAYS),
        )
        self.assertEqual(item.origin, SolicitudColectivo.Origin.INTERNAL)
        self.assertFalse(item.is_test)
        self.assertTrue(item.encrypted_snapshot)
        self.assertEqual(item.assigned_to, self.admin)

    def test_policy_workspace_contains_operational_sections(self):
        response = self.policy_page()
        for text in (
            "Detalle de póliza", "Resumen", "Grupo asegurado", "Cliente",
            "Respuestas recibidas", "Herramientas", "Actualizar información desde Zoho",
        ):
            self.assertContains(response, text)
        for legacy_text in (
            "Actividad reciente", "Novedades y solicitudes", "Revisión interna",
            "Crear solicitud multipóliza", "Preparar envío", "Próximamente",
            "Workspace de póliza", "Expediente", "Bandeja de solicitudes",
        ):
            self.assertNotContains(response, legacy_text)
        for legacy_route in ("solicitudes/construir", "solicitudes/crear"):
            self.assertNotContains(response, legacy_route)
        self.assertContains(response, 'class="policy-workspace-layout"', html=False)

    def test_workspace_actions_use_the_fresh_route_token_not_snapshot_token(self):
        response = self.policy_page(
            FakePolicyService(_policy(detail_token="stale-snapshot-capability"))
        )
        action = reverse(
            "cotizacion_colectivos:policy_generate_access_simple", args=[TOKEN],
        )
        self.assertContains(response, f'action="{action}"', html=False)
        self.assertNotContains(response, "stale-snapshot-capability")

    @patch("cotizacion_colectivos.views.PolicyService", return_value=FakePolicyService())
    def test_policy_workspace_can_revoke_its_live_access_without_exposing_an_id(self, _service):
        generate_url = reverse(
            "cotizacion_colectivos:policy_generate_access_simple", args=[TOKEN],
        )
        generated = self.client.post(
            generate_url, {"request_type": SolicitudColectivo.RequestType.UPDATE, "recipient": "cliente@example.test"},
        )
        self.assertContains(generated, "Copiar enlace")
        revoke_url = reverse("cotizacion_colectivos:policy_revoke_access", args=[TOKEN])
        self.assertNotIn(SolicitudColectivo.objects.get().public_id, revoke_url)
        response = self.client.post(revoke_url)
        self.assertRedirects(
            response, reverse("cotizacion_colectivos:policy_detail", args=[TOKEN]),
            fetch_redirect_response=False,
        )
        access = AccesoExternoSolicitudColectivo.objects.get()
        self.assertEqual(access.status, access.Status.REVOKED)

        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.admin)
        self.assertEqual(csrf_client.post(revoke_url).status_code, 403)

    @patch("cotizacion_colectivos.views.PolicyService", return_value=FakePolicyService())
    def test_revoked_or_expired_access_is_replaced_automatically(self, _service):
        url = reverse("cotizacion_colectivos:policy_generate_access_simple", args=[TOKEN])
        post = {"request_type": SolicitudColectivo.RequestType.UPDATE, "recipient": "cliente@example.test"}
        self.client.post(url, post)
        item = SolicitudColectivo.objects.get()
        first = item.external_accesses.get()
        first.status = first.Status.REVOKED
        first.save(update_fields=("status",))
        response = self.client.post(url, post)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Copiar enlace")
        self.assertEqual(item.external_accesses.count(), 2)
        current = item.external_accesses.order_by("-version").first()
        current.expires_at = timezone.now() - timedelta(seconds=1)
        current.save(update_fields=("expires_at",))
        response = self.client.post(url, post)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Copiar enlace")
        self.assertEqual(item.external_accesses.count(), 3)
        current.refresh_from_db()
        self.assertEqual(current.status, current.Status.EXPIRED)

    @patch("cotizacion_colectivos.views.PolicyService", return_value=FakePolicyService())
    def test_expired_internal_request_is_replaced_without_blocking_the_link(self, _service):
        expired = self.create_request()
        expired.deadline = timezone.localdate()
        expired.save(update_fields=("deadline",))

        response = self.client.post(
            reverse("cotizacion_colectivos:policy_generate_access_simple", args=[TOKEN]),
            {"request_type": SolicitudColectivo.RequestType.UPDATE, "recipient": "cliente@example.test"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Copiar enlace")
        self.assertEqual(SolicitudColectivo.objects.count(), 2)
        self.assertFalse(expired.external_accesses.exists())
        replacement = SolicitudColectivo.objects.exclude(pk=expired.pk).get()
        self.assertGreater(replacement.deadline, timezone.localdate())
        self.assertEqual(replacement.external_accesses.count(), 1)
        self.assertFalse(replacement.notifications.exists())

    def test_policy_page_displays_confirmed_functional_information(self):
        detail = _policy(
            retired_count=2,
            affiliate_count=3,
            beneficiary_count=4,
            plan_values=("Plan A", "Plan B"),
            economic_values=(("Prima", "100"), ("Valor asegurado", "200")),
            payment_calendar=(("Cuota 1", "2026-01-15"),),
        )
        response = self.policy_page(FakePolicyService(detail))
        for text in (
            "Vigencia", "Renovable", "Forma de pago",
            "Retirados", "Afiliados", "Beneficiarios", "Plan A", "Plan B",
            "Prima", "Valor asegurado", "Cuota 1", "Pago, plan y valores",
        ):
            self.assertContains(response, text)

    @patch("cotizacion_colectivos.views.PolicyService", return_value=FakePolicyService())
    def test_terminal_request_is_not_reopened(self, _service):
        terminal = self.create_request()
        terminal.status = terminal.Status.CLOSED
        terminal.closed_at = timezone.now()
        terminal.save(update_fields=("status", "closed_at"))
        response = self.client.post(
            reverse("cotizacion_colectivos:policy_generate_access_simple", args=[TOKEN]),
            {"request_type": SolicitudColectivo.RequestType.UPDATE, "recipient": "cliente@example.test"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Copiar enlace")
        self.assertEqual(SolicitudColectivo.objects.count(), 2)
        terminal.refresh_from_db()
        self.assertEqual(terminal.status, terminal.Status.CLOSED)

    @patch("cotizacion_colectivos.views.PolicyService", return_value=FakePolicyService())
    def test_answered_cycle_allows_a_new_direct_link_without_review(self, _service):
        answered = self.create_request()
        answered.status = answered.Status.ANSWERED
        answered.save(update_fields=("status",))

        response = self.client.post(
            reverse("cotizacion_colectivos:policy_generate_access_simple", args=[TOKEN]),
            {"request_type": SolicitudColectivo.RequestType.UPDATE, "recipient": "cliente@example.test"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Copiar enlace")
        self.assertEqual(SolicitudColectivo.objects.count(), 2)
        replacement = SolicitudColectivo.objects.exclude(pk=answered.pk).get()
        self.assertEqual(replacement.external_accesses.count(), 1)
        answered.refresh_from_db()
        self.assertEqual(answered.status, answered.Status.ANSWERED)

    def test_snapshot_records_are_inserted_in_bulk(self):
        manager = SolicitudColectivoRegistro.objects
        with patch.object(manager, "bulk_create", wraps=manager.bulk_create) as bulk_create:
            self.create_request()
        bulk_create.assert_called_once()
        self.assertEqual(bulk_create.call_args.kwargs["batch_size"], 500)

    def test_legacy_single_policy_endpoint_remains_post_only(self):
        url = reverse(
            "cotizacion_colectivos:policy_generate_access",
            args=[TOKEN, SolicitudColectivo.RequestType.RENEWAL],
        )
        self.assertEqual(self.client.get(url).status_code, 405)

    def test_manual_preparation_refresh_is_post_csrf_protected_and_forces_refresh(self):
        url = reverse("cotizacion_colectivos:policy_refresh_preparation", args=[TOKEN])
        service = Mock()
        with patch("cotizacion_colectivos.views.PolicyService", return_value=service):
            response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        service.group.assert_called_once_with(TOKEN, source_kind="company", refresh=True)
        self.assertEqual(self.client.get(url).status_code, 405)

        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.admin)
        self.assertEqual(csrf_client.post(url).status_code, 403)

    def test_legacy_single_policy_endpoint_keeps_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin)
        url = reverse(
            "cotizacion_colectivos:policy_generate_access",
            args=[TOKEN, SolicitudColectivo.RequestType.UPDATE],
        )
        self.assertEqual(client.post(url).status_code, 403)

    @patch(
        "cotizacion_colectivos.views.send_optional_invitation",
        side_effect=ExternalAccessError("delivery"),
    )
    def test_optional_email_failure_does_not_remove_link(self, _send):
        item = self.create_request()
        generated = generate_access(request=item, actor=self.admin)
        response = self.client.post(
            reverse("cotizacion_colectivos:request_external_access_email", args=[item.public_id]),
            {"recipient": "client@example.test", "access_token": generated.token},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(item.external_accesses.count(), 1)
        self.assertEqual(resolve_token(generated.token).pk, generated.access.pk)

    def test_missing_permissions_do_not_expose_administrative_workflow(self):
        restricted = Client()
        restricted.force_login(self.user)
        response = self.policy_page(client=restricted)
        self.assertNotContains(response, "Descargar Excel actual")
        self.assertNotContains(response, "Generar enlace")
        self.assertNotContains(response, "Sin permiso")
        self.assertNotContains(response, "solicitudes asociadas")

    def test_unclassified_policy_disables_excel_and_request_creation(self):
        response = self.policy_page(FakePolicyService(_policy(classification="unknown", branch_code="")))
        self.assertNotContains(response, "Descargar Excel actual")
        self.assertNotContains(response, "Generar enlace")
        self.assertNotContains(response, "Póliza no clasificada")
        with self.assertRaises(ColectivosServiceError):
            build_current_policy_workbook(
                TOKEN, FakePolicyService(_policy(classification="unknown", branch_code=""))
            )

    def test_active_request_is_linked_and_unrelated_policy_is_not_mixed(self):
        active = self.create_request()
        unrelated_policy = SolicitudColectivo.objects.create(
            source_kind="company",
            source_reference_hash=active.source_reference_hash,
            policy_reference_hash="f" * 64,
            encrypted_policy_token=active.encrypted_policy_token,
            masked_policy_reference="Referencia terminada en 9999",
            client_label=active.client_label,
            branch_code=active.branch_code,
            branch_name=active.branch_name,
            request_type=SolicitudColectivo.RequestType.RENEWAL,
            assigned_to=self.admin,
            deadline=active.deadline,
            zoho_profile="sandbox",
            encrypted_snapshot=active.encrypted_snapshot,
            created_by=self.admin,
        )
        unrelated_entity = SolicitudColectivo.objects.create(
            source_kind="person",
            source_reference_hash="e" * 64,
            policy_reference_hash=active.policy_reference_hash,
            encrypted_policy_token=active.encrypted_policy_token,
            masked_policy_reference=active.masked_policy_reference,
            client_label="Otra entidad",
            branch_code=active.branch_code,
            branch_name=active.branch_name,
            request_type=SolicitudColectivo.RequestType.UPDATE,
            assigned_to=self.admin,
            deadline=active.deadline,
            zoho_profile="sandbox",
            encrypted_snapshot=active.encrypted_snapshot,
            created_by=self.admin,
        )
        unrelated_profile = SolicitudColectivo.objects.create(
            source_kind="company",
            source_reference_hash=active.source_reference_hash,
            policy_reference_hash=active.policy_reference_hash,
            encrypted_policy_token=active.encrypted_policy_token,
            masked_policy_reference=active.masked_policy_reference,
            client_label=active.client_label,
            branch_code=active.branch_code,
            branch_name=active.branch_name,
            request_type=SolicitudColectivo.RequestType.UPDATE,
            assigned_to=self.admin,
            deadline=active.deadline,
            zoho_profile="production",
            encrypted_snapshot=active.encrypted_snapshot,
            created_by=self.admin,
        )
        response = self.policy_page()
        self.assertContains(response, "Generar enlace")
        self.assertNotContains(response, "Crear solicitud multipóliza")
        self.assertNotContains(response, "Ver solicitud")
        self.assertNotContains(response, active.public_id)
        self.assertNotContains(response, unrelated_policy.public_id)
        self.assertNotContains(response, unrelated_entity.public_id)
        self.assertNotContains(response, unrelated_profile.public_id)
        self.assertNotContains(response, "Generar enlace de actualización")
        self.assertNotContains(response, "Generar enlace de renovación")

    @patch("cotizacion_colectivos.views.PolicyService", return_value=FakePolicyService())
    def test_direct_policy_link_does_not_reuse_a_multipolicy_snapshot(self, _service):
        multipolicy = self.create_request()
        multipolicy.policy_reference_hash = "f" * 64
        multipolicy.record_count = 1001
        multipolicy.save(update_fields=("policy_reference_hash", "record_count"))
        first_policy = multipolicy.policies.get()
        first_policy.policy_reference_hash = "f" * 64
        first_policy.masked_policy_reference = "Referencia terminada en 9999"
        first_policy.save(
            update_fields=("policy_reference_hash", "masked_policy_reference")
        )
        SolicitudColectivoPoliza.objects.create(
            request=multipolicy,
            policy_reference_hash=request_reference_hashes(
                token=TOKEN, source_kind="company", holder="Empresa autorizada",
            )[0],
            encrypted_policy_token=multipolicy.encrypted_policy_token,
            masked_policy_reference="Referencia terminada en 3456",
            branch_code="91",
            branch_name="Salud colectivo",
            position=2,
        )
        policy_page = self.policy_page()
        self.assertContains(policy_page, "No generado")
        self.assertNotContains(policy_page, "Existe un enlace vigente")
        response = self.client.post(
            reverse("cotizacion_colectivos:policy_generate_access_simple", args=[TOKEN]),
            {"request_type": SolicitudColectivo.RequestType.UPDATE, "recipient": "cliente@example.test"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(SolicitudColectivo.objects.count(), 2)
        direct = SolicitudColectivo.objects.exclude(pk=multipolicy.pk).get()
        self.assertEqual(direct.record_count, 1)
        self.assertEqual(direct.policies.count(), 1)

    def test_builder_entity_token_rejects_tampering(self):
        token = sign_record_id(SOURCE_ID, "company")
        replacement = "A" if token[-1] != "A" else "B"
        with self.assertRaises(ColectivosServiceError):
            unsign_record_context(token[:-1] + replacement, "company")

    def test_legacy_multipolicy_builder_returns_to_entity_without_exposing_old_flow(self):
        item = self.create_request()
        company_token = sign_record_id(SOURCE_ID, "company")
        generated = type("Generated", (), {
            "url": "https://colectivos.example.test/solicitudes/colectivos/externa/opaque-token/",
            "token": "opaque-token",
            "access": type("Access", (), {"expires_at": timezone.now() + timedelta(days=1)})(),
        })()
        post = {
            "request_type": SolicitudColectivo.RequestType.UPDATE,
            "deadline": (timezone.localdate() + timedelta(days=5)).isoformat(),
            "confirm_snapshot": "on", "policy_0": "on",
            "adjustments_0": ("SIN_CAMBIOS", "INCLUSION", "RETIRO"),
        }
        with patch("cotizacion_colectivos.views.EntityDetailService", return_value=FakeEntityDetailService()), \
             patch("cotizacion_colectivos.views.create_request_from_policies", return_value=item), \
             patch("cotizacion_colectivos.views.generate_access", return_value=generated):
            response = self.client.post(
                reverse("cotizacion_colectivos:request_builder", args=["company", company_token]),
                post,
            )
            reload_response = self.client.get(
                reverse("cotizacion_colectivos:company_detail", args=[company_token])
            )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "cotizacion_colectivos/detail.html")
        self.assertContains(response, "Seleccione un ramo y el servicio que desea gestionar")
        self.assertNotContains(response, "Solicitud creada correctamente")
        self.assertNotContains(response, generated.url)
        self.assertNotContains(response, "Abrir solicitud")
        self.assertNotContains(response, "Modalidad: No determinada")
        self.assertNotContains(reload_response, generated.url)

    def test_builder_post_reuses_encrypted_get_preparation_without_entity_query(self):
        item = self.create_request()
        company_token = sign_record_id(SOURCE_ID, "company")
        url = reverse("cotizacion_colectivos:request_builder", args=["company", company_token])
        generated = type("Generated", (), {
            "url": "https://colectivos.example.test/solicitudes/colectivos/externa/opaque-token/",
            "token": "opaque-token",
            "access": type("Access", (), {"expires_at": timezone.now() + timedelta(days=1)})(),
        })()
        with patch("cotizacion_colectivos.views.EntityDetailService", return_value=FakeEntityDetailService()):
            self.assertEqual(self.client.get(url).status_code, 200)
        post = {
            "request_type": SolicitudColectivo.RequestType.UPDATE,
            "deadline": (timezone.localdate() + timedelta(days=5)).isoformat(),
            "confirm_snapshot": "on", "policy_0": "on",
            "adjustments_0": ("SIN_CAMBIOS", "INCLUSION", "RETIRO"),
        }
        with patch("cotizacion_colectivos.views.EntityDetailService") as detail_service, \
             patch("cotizacion_colectivos.views.create_request_from_policies", return_value=item), \
             patch("cotizacion_colectivos.views.generate_access", return_value=generated):
            response = self.client.post(url, post)
        detail_service.assert_not_called()
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "cotizacion_colectivos/generated_access.html")
        self.assertContains(response, "Solicitud creada correctamente")

    def test_request_detail_has_policy_return_and_external_access_section(self):
        item = self.create_request()
        response = self.client.get(
            reverse("cotizacion_colectivos:request_detail", args=[item.public_id])
        )
        self.assertContains(response, "Volver a la póliza")
        self.assertContains(response, "Acceso del cliente")
        self.assertContains(response, "No se ha generado un acceso externo")

    def test_generated_link_is_displayed_once_and_only_hash_is_persisted(self):
        item = self.create_request()
        item.status = item.Status.READY
        item.save(update_fields=("status",))
        response = self.client.post(
            reverse("cotizacion_colectivos:request_external_access", args=[item.public_id]),
            {
                "recipient": "cliente@example.test",
                "contact_name": "Cliente",
                "deadline": (timezone.localdate() + timedelta(days=8)).isoformat(),
                "intro": "",
                "instructions": "",
                "confirm_records": "on",
                "confirm_visible_fields": "on",
                "confirm_economic": "on",
                "confirm_snapshot": "on",
                "confirm_privacy": "on",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Enlace generado — copie ahora")
        match = re.search(rb'value="(https://colectivos\.example\.test/[^\"]+)"', response.content)
        self.assertIsNotNone(match)
        generated_url = match.group(1).decode()
        access = item.external_accesses.get()
        self.assertNotIn(generated_url, access.token_hash)
        self.assertEqual(resolve_token(generated_url.rstrip("/").rsplit("/", 1)[-1]).pk, access.pk)

        detail = self.client.get(
            reverse("cotizacion_colectivos:request_detail", args=[item.public_id])
        )
        self.assertNotContains(detail, generated_url)
        self.assertContains(detail, "Regenerar enlace")
        self.assertContains(detail, "Revocar enlace")

    def test_revocation_invalidates_token_and_requires_post_with_csrf(self):
        item = self.create_request()
        item.status = item.Status.READY
        item.save(update_fields=("status",))
        generated = generate_access(
            request=item, actor=self.admin, recipient="cliente@example.test"
        )
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.admin)
        url = reverse(
            "cotizacion_colectivos:request_external_access_revoke", args=[item.public_id]
        )
        self.assertEqual(csrf_client.get(url).status_code, 405)
        self.assertEqual(csrf_client.post(url).status_code, 403)
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        generated.access.refresh_from_db()
        self.assertEqual(generated.access.status, AccesoExternoSolicitudColectivo.Status.REVOKED)
        with self.assertRaises(ColectivosServiceError):
            # Typed internal references remain isolated from external access tokens.
            from cotizacion_colectivos.services.common import unsign_record_context

            unsign_record_context(generated.token, "policy")

    def test_send_now_requires_its_own_permission_before_creating_access(self):
        item = self.create_request()
        item.status = item.Status.READY
        item.save(update_fields=("status",))
        limited = get_user_model().objects.create_user(
            "access-generator", password="Password123!"
        )
        limited.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="cotizacion_colectivos",
                codename="generate_external_access",
            )
        )
        self.client.force_login(limited)
        response = self.client.post(
            reverse("cotizacion_colectivos:request_external_access", args=[item.public_id]),
            {
                "recipient": "cliente@example.test",
                "deadline": (timezone.localdate() + timedelta(days=8)).isoformat(),
                "confirm_records": "on",
                "confirm_visible_fields": "on",
                "confirm_economic": "on",
                "confirm_snapshot": "on",
                "confirm_privacy": "on",
                "send_now": "on",
            },
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(item.external_accesses.exists())

    @patch(
        "cotizacion_colectivos.views.send_invitation",
        side_effect=ExternalAccessError("No fue posible enviar la invitación."),
    )
    def test_failed_delivery_rolls_back_generated_access(self, _send):
        item = self.create_request()
        item.status = item.Status.READY
        item.save(update_fields=("status",))
        original_deadline = item.deadline
        response = self.client.post(
            reverse("cotizacion_colectivos:request_external_access", args=[item.public_id]),
            {
                "recipient": "cliente@example.test",
                "deadline": (original_deadline + timedelta(days=2)).isoformat(),
                "confirm_records": "on",
                "confirm_visible_fields": "on",
                "confirm_economic": "on",
                "confirm_snapshot": "on",
                "confirm_privacy": "on",
                "send_now": "on",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No fue posible enviar la invitación")
        item.refresh_from_db()
        self.assertEqual(item.deadline, original_deadline)
        self.assertFalse(item.external_accesses.exists())

    def test_masked_recipient_still_requires_personal_data_permission(self):
        item = self.create_request()
        item.status = item.Status.READY
        item.save(update_fields=("status",))
        generate_access(request=item, actor=self.admin, recipient="cliente@example.test")
        limited = get_user_model().objects.create_user(
            "request-viewer", password="Password123!"
        )
        limited.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="cotizacion_colectivos",
                codename="view_requests",
            )
        )
        self.client.force_login(limited)
        response = self.client.get(
            reverse("cotizacion_colectivos:request_detail", args=[item.public_id])
        )
        self.assertContains(response, "Oculto por permisos")
        self.assertNotContains(response, "c***@example.test")

    def test_regeneration_revokes_the_previous_link(self):
        item = self.create_request()
        item.status = item.Status.READY
        item.save(update_fields=("status",))
        first = generate_access(
            request=item, actor=self.admin, recipient="cliente@example.test"
        )
        second = generate_access(
            request=item,
            actor=self.admin,
            recipient="cliente@example.test",
            regenerate=True,
        )
        first.access.refresh_from_db()
        self.assertEqual(first.access.status, first.access.Status.REVOKED)
        self.assertEqual(resolve_token(second.token).pk, second.access.pk)
