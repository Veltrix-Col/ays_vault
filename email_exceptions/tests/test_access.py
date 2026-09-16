from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from unittest.mock import patch
from types import SimpleNamespace

from intranet_sso.delegated_access import DelegatedAccessResult
from vault.models import UserProfile
from email_exceptions.models import EmailException, ExceptionCase, InboundEmail
from email_exceptions.permissions import VIEW_PERMISSION
from intranet_sso.provisioning import get_or_create_intranet_user


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
        self.user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="email_exceptions",
                codename="operate_email_exceptions",
            ),
            Permission.objects.get(
                content_type__app_label="email_exceptions",
                codename="view_email_exceptions_operational",
            ),
        )
        self.sso_user = get_or_create_intranet_user("empleado@segurosays.com")
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
        self.sso_user.user_permissions.add(
            Permission.objects.get(content_type__app_label="email_exceptions", codename="view_email_exceptions_operational")
        )
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
        self.sso_user.user_permissions.add(
            Permission.objects.get(content_type__app_label="email_exceptions", codename="view_email_exceptions_operational")
        )
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
    def test_local_public_access_still_requires_module_permission(self):
        response = self.client.get(reverse("email_exceptions:list"))
        self.assertEqual(response.status_code, 403)
        self.assertFalse(response.wsgi_request.user.is_authenticated)

    @override_settings(
        DEBUG=False, RUNNING_TESTS=False, TOOLS_ACCESS_MODE="trusted_intranet",
        TOOLS_DELEGATED_ACCESS_VALIDATOR="email_exceptions.tests.test_access.allow_delegated_access",
    )
    def test_valid_sso_without_module_permission_is_denied(self):
        response = self.client.get(reverse("email_exceptions:list"), {"role": "operator", "is_staff": "true"})
        self.assertEqual(response.status_code, 403)
        self.assertTrue(response.wsgi_request.user.is_authenticated)
        self.assertFalse(response.wsgi_request.user.has_perm(VIEW_PERMISSION))

    @override_settings(
        DEBUG=False, RUNNING_TESTS=False, TOOLS_ACCESS_MODE="trusted_intranet",
        TOOLS_DELEGATED_ACCESS_VALIDATOR="email_exceptions.tests.test_access.allow_delegated_access",
    )
    def test_sso_viewer_can_read_but_cannot_mutate(self):
        self.sso_user.user_permissions.add(
            Permission.objects.get(content_type__app_label="email_exceptions", codename="view_email_exceptions_operational")
        )
        with patch("email_exceptions.views.responsible_options") as zoho_read:
            self.assertEqual(self.client.get(reverse("email_exceptions:list")).status_code, 200)
            self.assertEqual(self.client.get(reverse("email_exceptions:detail", args=[self.exception.pk])).status_code, 200)
            self.assertEqual(self.client.post(reverse("email_exceptions:ignore", args=[self.exception.pk]), {"reason": "Otro"}).status_code, 403)
            self.assertEqual(self.client.post(reverse("email_exceptions:create_task", args=[self.exception.pk]), {}).status_code, 403)
        zoho_read.assert_not_called()

    @override_settings(
        DEBUG=False, RUNNING_TESTS=False, TOOLS_ACCESS_MODE="trusted_intranet",
        TOOLS_DELEGATED_ACCESS_VALIDATOR="email_exceptions.tests.test_access.allow_delegated_access",
    )
    def test_sso_operator_can_read_and_post_action_under_module_guards(self):
        call_command("email_exceptions_access", subject="empleado@segurosays.com", role="operator")
        with patch("email_exceptions.views.task_creation_available", return_value=True), \
             patch("email_exceptions.views.responsible_options", return_value=[SimpleNamespace(actual_value="owner", display_value="Owner")]), \
             patch("email_exceptions.views.create_exception_task") as create_task:
            self.assertEqual(self.client.get(reverse("email_exceptions:list")).status_code, 200)
            response = self.client.post(reverse("email_exceptions:create_task", args=[self.exception.pk]), {
                "responsible": "owner", "subject": "Seguimiento", "due_date": "", "description": "Revisar",
            })
        self.assertEqual(response.status_code, 302)
        create_task.assert_called_once()

    def test_operator_permission_implies_view_and_command_assigns_or_revokes_module_groups(self):
        call_command("email_exceptions_access", subject="EMPLEADO@SEGUROSAYS.COM", role="operator")
        self.sso_user.refresh_from_db()
        self.assertTrue(self.sso_user.has_perm(VIEW_PERMISSION))
        self.assertTrue(self.sso_user.has_perm("email_exceptions.operate_email_exceptions"))
        self.assertFalse(self.sso_user.is_staff)
        self.assertFalse(self.sso_user.is_superuser)
        self.assertFalse(hasattr(self.sso_user, "vault_profile"))
        call_command("email_exceptions_access", subject="empleado@segurosays.com", role="none")
        self.sso_user = get_user_model().objects.get(pk=self.sso_user.pk)
        self.assertFalse(self.sso_user.has_perm(VIEW_PERMISSION))

    @override_settings(DEBUG=True, RUNNING_TESTS=True, TOOLS_ACCESS_MODE="local_public")
    def test_operator_actions_use_existing_post_flow_without_real_zoho(self):
        self.client.force_login(self.user)
        with patch("email_exceptions.views.task_creation_available", return_value=True), \
             patch("email_exceptions.views.responsible_options", return_value=[SimpleNamespace(actual_value="owner", display_value="Owner")]), \
             patch("email_exceptions.views.create_exception_task") as publisher:
            response = self.client.post(reverse("email_exceptions:create_task", args=[self.exception.pk]), {
                "responsible": "owner", "subject": "Seguimiento", "due_date": "", "description": "Revisar",
            })
        self.assertEqual(response.status_code, 302)
        publisher.assert_called_once()

    @override_settings(DEBUG=True, RUNNING_TESTS=True, TOOLS_ACCESS_MODE="local_public")
    def test_viewer_and_disabled_operator_detail_do_not_read_zoho_metadata(self):
        self.user.user_permissions.remove(Permission.objects.get(content_type__app_label="email_exceptions", codename="operate_email_exceptions"))
        case = ExceptionCase.objects.create(case_key="access-viewer-case", opened_at=self.email.received_at, last_activity_at=self.email.received_at)
        self.exception.case = case
        self.exception.save(update_fields=("case",))
        self.client.force_login(self.user)
        with patch("email_exceptions.views.responsible_options") as zoho_read, \
             patch("email_exceptions.views.task_creation_available", return_value=False):
            self.assertEqual(self.client.get(reverse("email_exceptions:detail", args=[self.exception.pk])).status_code, 200)
            self.assertEqual(self.client.get(reverse("email_exceptions:case_detail", args=[case.pk])).status_code, 200)
        zoho_read.assert_not_called()

    @override_settings(DEBUG=True, RUNNING_TESTS=True, TOOLS_ACCESS_MODE="local_public")
    def test_operator_with_task_guard_disabled_does_not_read_zoho_metadata(self):
        case = ExceptionCase.objects.create(case_key="access-operator-case", opened_at=self.email.received_at, last_activity_at=self.email.received_at)
        self.exception.case = case
        self.exception.save(update_fields=("case",))
        self.client.force_login(self.user)
        with patch("email_exceptions.views.task_creation_available", return_value=False), \
             patch("email_exceptions.views.responsible_options") as zoho_read:
            response = self.client.get(reverse("email_exceptions:case_detail", args=[case.pk]))
        self.assertEqual(response.status_code, 200)
        zoho_read.assert_not_called()

    @override_settings(DEBUG=True, RUNNING_TESTS=True, TOOLS_ACCESS_MODE="local_public")
    def test_direct_exception_and_case_urls_require_view_permission(self):
        self.user.user_permissions.clear()
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse("email_exceptions:detail", args=[self.exception.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("email_exceptions:case_detail", args=[1])).status_code, 403)

    @override_settings(DEBUG=True, RUNNING_TESTS=True, TOOLS_ACCESS_MODE="local_public")
    def test_vault_role_alone_does_not_grant_module_read_permission(self):
        self.user.user_permissions.clear()
        self.user.vault_profile.role = UserProfile.ADMIN
        self.user.vault_profile.save(update_fields=("role",))
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse("email_exceptions:detail", args=[self.exception.pk])).status_code, 403)

    @override_settings(DEBUG=True, RUNNING_TESTS=True, TOOLS_ACCESS_MODE="local_public")
    def test_case_timeline_action_prefixes_are_accepted_by_existing_exception_endpoints(self):
        case = ExceptionCase.objects.create(
            case_key="access-case-action",
            opened_at=self.email.received_at,
            last_activity_at=self.email.received_at,
        )
        self.exception.case = case
        self.exception.save(update_fields=("case",))
        self.client.force_login(self.user)
        prefix = f"exception-{self.exception.pk}-"
        with patch("email_exceptions.views.task_creation_available", return_value=True), \
             patch("email_exceptions.views.responsible_options", return_value=[SimpleNamespace(actual_value="owner", display_value="Owner")]), \
             patch("email_exceptions.views.create_exception_task") as create_task:
            response = self.client.post(reverse("email_exceptions:create_task", args=[self.exception.pk]), {
                f"{prefix}responsible": "owner", f"{prefix}subject": "Seguimiento",
                f"{prefix}due_date": "", f"{prefix}description": "Revisar",
            })
        self.assertEqual(response.status_code, 302)
        create_task.assert_called_once()

        response = self.client.post(reverse("email_exceptions:ignore", args=[self.exception.pk]), {
            f"{prefix}reason": "Duplicado", f"{prefix}comment": "Repetido",
        })
        self.assertEqual(response.status_code, 302)
        self.exception.refresh_from_db()
        self.assertEqual(self.exception.status, EmailException.IGNORED)

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
            data='{"source_mailbox":"comunicaciones@segurosays.com","message_id":"trusted-m2m-1","subject":"Comprobante de pago póliza BEMSA 123"}',
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
        self.user.user_permissions.clear()
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

    @override_settings(DEBUG=True, RUNNING_TESTS=True, TOOLS_ACCESS_MODE="local_public")
    def test_task_post_requires_csrf_and_does_not_reach_publisher(self):
        self.client.force_login(self.user)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        with patch("email_exceptions.views.create_exception_task") as publisher:
            response = csrf_client.post(
                reverse("email_exceptions:create_task", args=[self.exception.pk]),
                {"responsible": "owner", "subject": "Seguimiento", "description": "Revisar"},
            )
        self.assertEqual(response.status_code, 403)
        publisher.assert_not_called()
