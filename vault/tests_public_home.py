from django.conf import settings
from django.contrib.auth import get_user_model
from django.template.loader import render_to_string
from django.test import TestCase, override_settings
from django.urls import resolve, reverse

from portal.views import area_home, public_home
from portal.catalog import application_catalog, area_catalog
from cotizacion_colectivos import views as colectivos_views
from vault.forms import MFAEnrollmentForm, OTPVerificationForm


class PublicHomeTests(TestCase):
    def test_root_is_public_home_and_vault_uses_protected_entry(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(resolve("/").func, public_home)
        self.assertTemplateUsed(response, "portal/home.html")
        self.assertFalse(response.has_header("Location"))
        self.assertContains(response, "Banco de Herramientas")
        self.assertNotContains(response, "Portal de Aplicaciones")
        self.assertContains(response, "Seleccione el área o busque la herramienta que necesita.")
        self.assertContains(response, f'href="{reverse("vault:dashboard")}"')
        self.assertEqual(reverse("login"), "/login/")
        self.assertEqual(reverse("vault:dashboard"), "/vault/")

    def test_root_remains_portal_for_authenticated_user(self):
        user = get_user_model().objects.create_user("portal.autenticado")
        self.client.force_login(user)
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "portal/home.html")
        self.assertFalse(response.has_header("Location"))
        self.assertContains(response, "Banco de Herramientas")
        self.assertContains(response, "CardManager")

    def test_root_ignores_invalid_session_cookie(self):
        self.client.cookies[settings.SESSION_COOKIE_NAME] = "invalid-or-revoked-session"
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "portal/home.html")
        self.assertFalse(response.has_header("Location"))

    def test_vault_entry_uses_existing_authentication_guard(self):
        response = self.client.get(reverse("vault:dashboard"))
        self.assertRedirects(
            response,
            f'{reverse("login")}?next={reverse("vault:dashboard")}',
            fetch_redirect_response=False,
        )

    def test_other_vault_route_remains_protected(self):
        response = self.client.get(reverse("vault:card_list"))
        self.assertRedirects(
            response,
            f'{reverse("login")}?next={reverse("vault:card_list")}',
            fetch_redirect_response=False,
        )

    def test_catalog_renders_authorized_applications_without_system_information(self):
        response = self.client.get("/")
        self.assertContains(response, 'class="application-card area-package"', count=3)
        self.assertContains(response, 'data-tool-card', count=6)
        self.assertContains(response, "CardManager")
        self.assertContains(response, ">SOAT<")
        self.assertContains(response, ">Novedades<")
        self.assertContains(response, ">Cotización Individual<")
        self.assertContains(response, ">Invitaciones a Aseguradoras<")
        self.assertContains(response, "Conciliador de Facturación")
        self.assertNotContains(response, "Solicitudes y Renovaciones")
        self.assertNotContains(response, "Cotización – Colectivos")
        self.assertNotContains(response, 'class="application-logo application-logo--cardmanager"')
        self.assertContains(response, 'class="application-icon"', count=3)
        self.assertNotContains(response, "Centro de Control")
        self.assertNotContains(response, "Correo y destinatarios")
        self.assertNotContains(response, "Cerrar sesión")
        self.assertContains(response, "Buscar herramientas")
        self.assertContains(response, 'data-tool-search')
        self.assertContains(response, "Cartera")

    def test_catalog_uses_business_areas_without_crossing_colectivos(self):
        response = self.client.get("/")
        areas = {
            area["name"]: tuple(app["name"] for app in area["applications"])
            for area in response.context["area_packages"]
        }
        self.assertEqual(
            areas,
            {
                "Cartera": (
                    "CardManager",
                ),
                "Operaciones": ("SOAT",),
                "Colectivos": (
                    "Novedades",
                    "Cotización Individual",
                    "Invitaciones a Aseguradoras",
                    "Conciliador de Facturación",
                ),
            },
        )
        self.assertNotIn("Colectivos", {app["name"] for app in application_catalog()})

    def test_root_first_level_contains_all_area_packages(self):
        response = self.client.get("/")
        self.assertEqual(
            tuple(area["name"] for area in response.context["area_packages"]),
            ("Cartera", "Operaciones", "Colectivos"),
        )
        self.assertContains(response, f'href="{reverse("area_home", args=["cartera"])}"')
        self.assertContains(response, f'href="{reverse("area_home", args=["operaciones"])}"')
        self.assertContains(response, f'href="{reverse("area_home", args=["colectivos"])}"')
        self.assertContains(response, "1 herramientas")
        self.assertContains(response, "4 herramientas")

    def test_area_subhomes_have_the_exact_taxonomy(self):
        expected = {
            "cartera": ("CardManager",),
            "operaciones": ("SOAT",),
            "colectivos": (
                "Novedades",
                "Cotización Individual",
                "Invitaciones a Aseguradoras",
                "Conciliador de Facturación",
            ),
        }
        for slug, names in expected.items():
            with self.subTest(area=slug):
                response = self.client.get(reverse("area_home", args=[slug]))
                self.assertEqual(response.status_code, 200)
                self.assertIs(resolve(reverse("area_home", args=[slug])).func, area_home)
                self.assertTemplateUsed(response, "portal/area_home.html")
                self.assertEqual(tuple(app["name"] for app in response.context["area"]["applications"]), names)
                self.assertContains(response, 'class="application-card"', count=len(names))

        colectivos = self.client.get(reverse("area_home", args=["colectivos"]))
        self.assertNotContains(colectivos, '<h2>Colectivos</h2>')
        self.assertContains(colectivos, "Centro operativo")
        self.assertContains(colectivos, "Bandeja de solicitudes")
        self.assertContains(
            colectivos,
            f'href="{reverse("cotizacion_colectivos:request_list")}"',
        )
        self.assertContains(colectivos, 'class="application-card"', count=4)
        cartera = self.client.get(reverse("area_home", args=["cartera"]))
        self.assertNotContains(cartera, "Centro operativo")
        self.assertNotContains(cartera, "SOAT")
        for forbidden in ("Novedades", "Cotización Individual", "Invitaciones", "Conciliador"):
            self.assertNotContains(cartera, forbidden)
        operaciones = self.client.get(reverse("area_home", args=["operaciones"]))
        self.assertContains(operaciones, "SOAT")
        self.assertContains(operaciones, "Área Operaciones")

    def test_unknown_area_is_not_inferred(self):
        self.assertEqual(self.client.get("/areas/desconocida/").status_code, 404)

    def test_collective_cards_enter_existing_contextual_modes(self):
        urls = {app["name"]: app["url"] for app in application_catalog()}
        self.assertEqual(urls["Novedades"], reverse("cotizacion_colectivos:novelties_index"))
        self.assertEqual(urls["Cotización Individual"], reverse("cotizacion_colectivos:individual_index"))
        self.assertEqual(urls["Invitaciones a Aseguradoras"], reverse("cotizacion_colectivos:invitations_index"))
        for route_name in (
            "cotizacion_colectivos:novelties_client_search",
            "cotizacion_colectivos:individual_client_search",
            "cotizacion_colectivos:invitations_client_search",
        ):
            with self.subTest(route=route_name):
                self.assertIs(resolve(reverse(route_name)).func, colectivos_views.client_search)

    def test_search_is_local_accent_insensitive_and_keyboard_accessible(self):
        script = (settings.BASE_DIR / "static" / "js" / "portal.js").read_text(encoding="utf-8")
        self.assertIn('.normalize("NFD")', script)
        self.assertIn('input.addEventListener("input", filter)', script)
        for key in ("Escape", "ArrowDown", "ArrowUp", "Enter"):
            self.assertIn(key, script)
        self.assertNotIn("fetch(", script)
        self.assertIn("packages.hidden = searching", script)
        self.assertIn("results.hidden = !searching", script)

    @override_settings(SOAT_APP_URL="")
    def test_soat_uses_internal_module_when_external_url_is_not_configured(self):
        response = self.client.get("/")
        self.assertContains(response, f'href="{reverse("soat:upload")}"')
        self.assertNotContains(response, "Acceso no configurado")

    @override_settings(SOAT_APP_URL="https://soat.example.invalid/access")
    def test_soat_uses_configured_http_url(self):
        response = self.client.get("/")
        self.assertContains(response, 'href="https://soat.example.invalid/access"')
        self.assertContains(response, 'rel="noopener noreferrer"')

    @override_settings(SOAT_APP_URL="javascript:alert(1)")
    def test_soat_rejects_unsafe_configured_url(self):
        response = self.client.get("/")
        self.assertContains(response, f'href="{reverse("soat:upload")}"')
        self.assertNotContains(response, "javascript:")

    def test_authentication_and_blocked_access_screens_return_to_portal(self):
        contexts = {
            "registration/login.html": {"form": None},
            "registration/mfa_verify.html": {"form": OTPVerificationForm()},
            "registration/mfa_enroll.html": {
                "form": MFAEnrollmentForm(),
                "manual_key": "",
                "qr_data_uri": "",
            },
            "registration/recovery_codes.html": {"codes": [], "first_display": False},
            "vault/access_denied.html": {},
        }
        for template_name, context in contexts.items():
            with self.subTest(template=template_name):
                content = render_to_string(template_name, context)
                self.assertIn('href="/"', content)
                self.assertIn("Volver al Banco de Herramientas", content)

    def test_cardmanager_authentication_templates_use_the_official_brand(self):
        contexts = {
            "registration/login.html": {"form": None},
            "registration/mfa_verify.html": {"form": OTPVerificationForm()},
            "registration/mfa_enroll.html": {
                "form": MFAEnrollmentForm(),
                "manual_key": "",
                "qr_data_uri": "",
            },
        }
        expected = "/static/img/branding/cardmanager/Logo-CardManager-CO-COLOR.png"
        for template_name, context in contexts.items():
            with self.subTest(template=template_name):
                content = render_to_string(template_name, context)
                self.assertIn(expected, content)
                self.assertIn('alt="CardManager"', content)
                self.assertNotIn("/static/img/branding/logo-ays-azul.png", content)
