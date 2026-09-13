from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from intranet_sso.delegated_access import DelegatedAccessResult
from vault.models import UserProfile
from email_exceptions.models import EmailException, InboundEmail


def allow_delegated_access(*, request, application):
    return DelegatedAccessResult(
        allowed=application == "email_exceptions",
        category="delegated_cookie",
        subject="empleado@segurosays.com",
    )


class EmailExceptionsAccessTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="operator", password="SafePassword123!")
        UserProfile.objects.update_or_create(
            user=self.user,
            defaults={"role": UserProfile.LEADER, "active": True},
        )
        self.email = InboundEmail.objects.create(
            source_mailbox="comunicaciones@segurosays.com",
            external_message_id="access-detail-1",
            received_at="2026-09-12T10:32:00Z",
            subject="Detalle de excepción",
            body_text="Contenido sintético",
        )
        self.exception = EmailException.objects.create(
            primary_email=self.email,
            last_subject=self.email.subject,
            exception_reason="Motivo sintético",
            status=EmailException.PENDING,
        )

    @override_settings(
        DEBUG=False,
        RUNNING_TESTS=False,
        TOOLS_ACCESS_MODE="trusted_intranet",
        TOOLS_DELEGATED_ACCESS_VALIDATOR="email_exceptions.tests.test_access.allow_delegated_access",
        COLECTIVOS_INTERNAL_PUBLIC_ACCESS=False,
    )
    def test_valid_intranet_context_views_list_without_django_login_redirect(self):
        response = self.client.get(reverse("email_exceptions:list"))
        self.assertNotEqual(response.status_code, 302)
        self.assertEqual(response.status_code, 200)

    @override_settings(
        DEBUG=False,
        RUNNING_TESTS=False,
        TOOLS_ACCESS_MODE="trusted_intranet",
        TOOLS_DELEGATED_ACCESS_VALIDATOR="email_exceptions.tests.test_access.allow_delegated_access",
        COLECTIVOS_INTERNAL_PUBLIC_ACCESS=False,
    )
    def test_valid_intranet_context_views_detail_without_django_login_redirect(self):
        response = self.client.get(reverse("email_exceptions:detail", args=[self.exception.pk]))
        self.assertEqual(response.status_code, 200)

    def test_card_manager_and_admin_keep_their_own_authentication(self):
        card_response = self.client.get(reverse("vault:card_list"))
        admin_response = self.client.get("/admin/")
        self.assertEqual(card_response.status_code, 302)
        self.assertIn("/login/", card_response["Location"])
        self.assertEqual(admin_response.status_code, 302)
        self.assertIn("/admin/login/", admin_response["Location"])

    @override_settings(DEBUG=True, RUNNING_TESTS=True, TOOLS_ACCESS_MODE="local_public")
    def test_local_navigation_matches_operational_inherited_access_with_anonymous_user(self):
        response = self.client.get(reverse("email_exceptions:list"))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.wsgi_request.user.is_authenticated)

    @override_settings(
        DEBUG=True, RUNNING_TESTS=True, TOOLS_ACCESS_MODE="local_public",
        EMAIL_EXCEPTIONS_INBOUND_TOKEN="access-test-token",
        EMAIL_EXCEPTIONS_ENABLED=True, EMAIL_EXCEPTIONS_INBOUND_ENABLED=True,
    )
    def test_inbound_remains_m2m_token_protected(self):
        url = reverse("email_exceptions:inbound")
        self.assertEqual(self.client.post(url, data="{}", content_type="application/json").status_code, 401)

    @override_settings(
        DEBUG=False,
        RUNNING_TESTS=False,
        TOOLS_ACCESS_MODE="trusted_intranet",
        TOOLS_DELEGATED_ACCESS_VALIDATOR="intranet_sso.delegated_access.validate_intranet_session",
        EMAIL_EXCEPTIONS_ENABLED=True,
        EMAIL_EXCEPTIONS_INBOUND_ENABLED=True,
        EMAIL_EXCEPTIONS_INBOUND_TOKEN="production-like-token",
        EMAIL_EXCEPTIONS_ALLOWED_MAILBOXES=("comunicaciones@segurosays.com",),
        COLECTIVOS_INTERNAL_PUBLIC_ACCESS=False,
    )
    def test_inbound_valid_token_without_sso_reaches_m2m_view(self):
        response = self.client.post(
            reverse("email_exceptions:inbound"),
            data='{"source_mailbox":"comunicaciones@segurosays.com","message_id":"trusted-m2m-1","subject":"Comprobante de pago"}',
            content_type="application/json",
            HTTP_X_EMAIL_EXCEPTIONS_TOKEN="production-like-token",
        )
        self.assertEqual(response.status_code, 201)
        self.assertIsNotNone(response.json()["exception_id"])

    @override_settings(
        DEBUG=False,
        RUNNING_TESTS=False,
        TOOLS_ACCESS_MODE="trusted_intranet",
        TOOLS_DELEGATED_ACCESS_VALIDATOR="intranet_sso.delegated_access.validate_intranet_session",
        EMAIL_EXCEPTIONS_ENABLED=True,
        EMAIL_EXCEPTIONS_INBOUND_ENABLED=True,
        EMAIL_EXCEPTIONS_INBOUND_TOKEN="production-like-token",
        EMAIL_EXCEPTIONS_ALLOWED_MAILBOXES=("comunicaciones@segurosays.com",),
        COLECTIVOS_INTERNAL_PUBLIC_ACCESS=False,
    )
    def test_inbound_invalid_token_without_sso_returns_401(self):
        response = self.client.post(
            reverse("email_exceptions:inbound"),
            data='{"source_mailbox":"comunicaciones@segurosays.com","message_id":"trusted-m2m-2","subject":"Comprobante"}',
            content_type="application/json",
            HTTP_X_EMAIL_EXCEPTIONS_TOKEN="wrong-token",
        )
        self.assertEqual(response.status_code, 401)

    @override_settings(
        DEBUG=False,
        RUNNING_TESTS=False,
        TOOLS_ACCESS_MODE="trusted_intranet",
        TOOLS_DELEGATED_ACCESS_VALIDATOR="intranet_sso.delegated_access.validate_intranet_session",
        EMAIL_EXCEPTIONS_ENABLED=True,
        EMAIL_EXCEPTIONS_INBOUND_ENABLED=True,
        EMAIL_EXCEPTIONS_INBOUND_TOKEN="production-like-token",
        EMAIL_EXCEPTIONS_ALLOWED_MAILBOXES=("comunicaciones@segurosays.com",),
        COLECTIVOS_INTERNAL_PUBLIC_ACCESS=False,
    )
    def test_inbound_disallowed_mailbox_without_sso_returns_403(self):
        response = self.client.post(
            reverse("email_exceptions:inbound"),
            data='{"source_mailbox":"otra@invalid.test","message_id":"trusted-m2m-3","subject":"Comprobante"}',
            content_type="application/json",
            HTTP_X_EMAIL_EXCEPTIONS_TOKEN="production-like-token",
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"], "mailbox_not_allowed")

    @override_settings(DEBUG=False, RUNNING_TESTS=False, TOOLS_ACCESS_MODE="trusted_intranet")
    def test_production_without_intranet_context_is_forbidden(self):
        self.assertEqual(self.client.get(reverse("email_exceptions:list")).status_code, 403)

    @override_settings(DEBUG=True, RUNNING_TESTS=True, TOOLS_ACCESS_MODE="local_public")
    def test_unauthorized_operator_cannot_ignore_exception(self):
        self.user.vault_profile.active = False
        self.user.vault_profile.save(update_fields=["active"])
        response = self.client.post(
            reverse("email_exceptions:ignore", args=[self.exception.pk]),
            {"reason": "Duplicado", "comment": "No aplica"},
        )
        self.assertEqual(response.status_code, 403)
        self.exception.refresh_from_db()
        self.assertEqual(self.exception.status, EmailException.PENDING)

    @override_settings(DEBUG=True, RUNNING_TESTS=True, TOOLS_ACCESS_MODE="local_public")
    def test_authorized_operator_can_ignore_exception(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("email_exceptions:ignore", args=[self.exception.pk]),
            {"reason": "Duplicado", "comment": "No aplica"},
        )
        self.assertEqual(response.status_code, 302)
        self.exception.refresh_from_db()
        self.assertEqual(self.exception.status, EmailException.IGNORED)

    @override_settings(DEBUG=True, RUNNING_TESTS=True, TOOLS_ACCESS_MODE="local_public")
    def test_ignore_requires_post_and_csrf(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse("email_exceptions:ignore", args=[self.exception.pk])).status_code, 405)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        response = csrf_client.post(
            reverse("email_exceptions:ignore", args=[self.exception.pk]),
            {"reason": "Duplicado", "comment": "No aplica"},
        )
        self.assertEqual(response.status_code, 403)
