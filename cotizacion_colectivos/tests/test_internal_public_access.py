from __future__ import annotations

from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ImproperlyConfigured
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from cotizacion_colectivos.actors import get_internal_actor
from cotizacion_colectivos.context_processors import colectivos_navigation
from cotizacion_colectivos.forms import RequestCreateForm, RequestEditForm, RequestFilterForm
from cotizacion_colectivos.models import NotificacionColectivos, SolicitudColectivo
from cotizacion_colectivos.permissions import has_internal_permission


PUBLIC = {
    "DEBUG": True,
    "TOOLS_ACCESS_MODE": "local_public",
    "COLECTIVOS_INTERNAL_PUBLIC_ACCESS": True,
    "COLECTIVOS_TECHNICAL_ACTOR_USERNAME": "colectivos-technical-test",
}


@override_settings(**PUBLIC)
class InternalPublicAccessTests(TestCase):
    def setUp(self):
        self.request = RequestFactory().post("/cotizacion-colectivos/")
        self.request.user = AnonymousUser()

    def test_internal_index_is_public_but_csrf_remains_required(self):
        self.assertEqual(self.client.get(reverse("cotizacion_colectivos:index")).status_code, 200)
        csrf_client = Client(enforce_csrf_checks=True)
        self.assertEqual(
            csrf_client.post(reverse("cotizacion_colectivos:company_search"), {"query": "Empresa"}).status_code,
            403,
        )

    def test_django_permissions_are_not_required_in_public_mode(self):
        self.assertTrue(has_internal_permission(self.request, "approve_requests"))
        response = self.client.get(reverse("cotizacion_colectivos:request_list"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Sin permiso")
        self.assertNotContains(response, "Debe iniciar sesi")
        legacy = self.client.get(reverse("cotizacion_colectivos:notification_list"))
        self.assertRedirects(
            legacy,
            reverse("cotizacion_colectivos:request_list"),
            fetch_redirect_response=False,
        )

    def test_forms_do_not_expose_user_assignment_in_public_mode(self):
        self.assertNotIn("assigned_to", RequestCreateForm(public_access=True).fields)
        self.assertNotIn("assigned_to", RequestEditForm(public_access=True).fields)
        filters = RequestFilterForm(public_access=True)
        self.assertNotIn("assigned_to", filters.fields)
        self.assertNotIn("assigned_to_me", filters.fields)
        self.assertIn("task_responsible", filters.fields)

    def test_configured_technical_actor_is_non_privileged_and_not_anonymous(self):
        actor = get_internal_actor(self.request, create=True)
        self.assertEqual(actor.username, "colectivos-technical-test")
        self.assertTrue(actor.is_active)
        self.assertFalse(actor.is_staff)
        self.assertFalse(actor.is_superuser)
        self.assertFalse(actor.has_usable_password())

    def test_existing_privileged_or_interactive_account_is_rejected(self):
        get_user_model().objects.create_superuser(
            "colectivos-technical-test", "technical@example.test", "Password123!"
        )
        with self.assertRaises(ImproperlyConfigured):
            get_internal_actor(self.request, create=True)

    @override_settings(COLECTIVOS_INTERNAL_PUBLIC_ACCESS=False)
    def test_future_inherited_mode_keeps_normal_permission_checks(self):
        self.assertFalse(has_internal_permission(self.request, "view_requests"))


@override_settings(
    COLECTIVOS_INTERNAL_PUBLIC_ACCESS=False,
    COLECTIVOS_TECHNICAL_ACTOR_USERNAME="colectivos-technical-test",
)
class NotificationNavigationActorTests(TestCase):
    def _notification(self, user):
        request = SolicitudColectivo.objects.create(
            public_id="COL-NOTIFICATION-TEST",
            source_kind="company",
            source_reference_hash="a" * 64,
            policy_reference_hash="b" * 64,
            encrypted_policy_token="token",
            masked_policy_reference="Póliza 1234",
            client_label="Cliente de prueba",
            branch_code="91",
            branch_name="VG deudores",
            request_type=SolicitudColectivo.RequestType.UPDATE,
            assigned_to=user,
            deadline=timezone.localdate(),
            zoho_profile="sandbox",
            encrypted_snapshot="{}",
            created_by=user,
        )
        return NotificacionColectivos.objects.create(
            user=user,
            request=request,
            notification_type="CLIENT_RESPONSE",
            title="Respuesta recibida",
            message="Nueva respuesta",
            deduplication_key="notification-test",
        )

    def test_authenticated_user_gets_unread_count(self):
        user = get_user_model().objects.create_user("internal-user", is_active=True)
        request = RequestFactory().get("/")
        request.user = user
        self._notification(user)

        self.assertEqual(colectivos_navigation(request)["colectivos_unread_notifications"], 1)

    def test_valid_delegated_anonymous_request_gets_unread_count(self):
        actor = get_user_model().objects.create_user("colectivos-technical-test", is_active=True)
        actor.set_unusable_password()
        actor.save(update_fields=("password",))
        request = RequestFactory().get("/")
        request.user = AnonymousUser()
        request.delegated_access = SimpleNamespace(allowed=True)
        request.inherited_tool_application = "cotizacion_colectivos"
        self._notification(actor)

        self.assertEqual(colectivos_navigation(request)["colectivos_unread_notifications"], 1)

    def test_anonymous_request_without_delegation_gets_zero(self):
        request = RequestFactory().get("/")
        request.user = AnonymousUser()

        self.assertEqual(colectivos_navigation(request)["colectivos_unread_notifications"], 0)
