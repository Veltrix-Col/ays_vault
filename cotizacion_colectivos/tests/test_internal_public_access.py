from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ImproperlyConfigured
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse

from cotizacion_colectivos.actors import get_internal_actor
from cotizacion_colectivos.forms import RequestCreateForm, RequestEditForm, RequestFilterForm
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
        for route in ("request_list", "notification_list"):
            with self.subTest(route=route):
                response = self.client.get(reverse(f"cotizacion_colectivos:{route}"))
                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, "Sin permiso")
                self.assertNotContains(response, "Debe iniciar sesi")

    def test_forms_do_not_expose_user_assignment_in_public_mode(self):
        self.assertNotIn("assigned_to", RequestCreateForm(public_access=True).fields)
        self.assertNotIn("assigned_to", RequestEditForm(public_access=True).fields)
        filters = RequestFilterForm(public_access=True)
        self.assertNotIn("assigned_to", filters.fields)
        self.assertNotIn("assigned_to_me", filters.fields)

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
