import os
import tempfile

from django.conf import settings
from django.db import connections
from django.db.backends.sqlite3.base import DatabaseWrapper
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class Migration0009SQLiteTests(TransactionTestCase):
    reset_sequences = True

    def test_0008_to_0009_preserves_legacy_data_before_removal(self):
        alias = "default"
        original_connection = connections[alias]
        temporary_settings = settings.DATABASES[alias].copy()
        temporary_database = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        temporary_database.close()
        temporary_settings["NAME"] = temporary_database.name
        temporary_settings["TEST"] = {**temporary_settings.get("TEST", {}), "NAME": temporary_database.name}
        connection = DatabaseWrapper(temporary_settings, alias)
        connections._connections.default = connection
        executor = MigrationExecutor(connection)
        target_0008 = [("vault", "0008_paymentcard_encrypted_company_and_more")]
        target_0009 = [("vault", "0009_separate_identity_window_and_operation_context")]
        try:
            executor.migrate(target_0008)
            apps_0008 = executor.loader.project_state(target_0008).apps
            User = apps_0008.get_model("auth", "User")
            Window = apps_0008.get_model("vault", "SensitiveOperationWindow")
            Card = apps_0008.get_model("vault", "PaymentCard")
            Grant = apps_0008.get_model("vault", "RevealGrant")
            user = User.objects.using(alias).create(username="migration-0009")
            orphan_user = User.objects.using(alias).create(username="migration-0009-orphan")
            window_with_grant = Window.objects.using(alias).create(
                user_id=user.pk, session_hash="session-with-grant", purpose="protected_data",
                reason="Motivo histórico", internal_reference="REF-001",
                expires_at="2099-01-01T00:00:00Z",
            )
            window_without_grant = Window.objects.using(alias).create(
                user_id=user.pk, session_hash="session-without-grant", purpose="protected_data",
                reason="", internal_reference="",
                expires_at="2099-01-01T00:00:00Z",
            )
            card = Card.objects.using(alias).create(
                client_name="Cliente sintético", cardholder_name="Titular sintético", brand="VISA",
                encrypted_pan="legacy-pan", pan_fingerprint="migration-fingerprint-001", last4="1111", encrypted_expiry="legacy-expiry",
                purpose="migration-test", created_by_id=user.pk,
            )
            Grant.objects.using(alias).create(
                token_hash="migration-token-hash", field_name="pan", reason="Motivo grant",
                internal_reference="GRANT-001", session_key="session-with-grant",
                expires_at="2099-01-01T00:00:00Z", card_id=card.pk, user_id=user.pk,
                operation_window_id=window_with_grant.pk,
            )
            orphan_card = Card.objects.using(alias).create(
                client_name="Cliente huérfano sintético", cardholder_name="Titular huérfano sintético", brand="VISA",
                encrypted_pan="orphan-pan-2", pan_fingerprint="migration-fingerprint-002", last4="2222", encrypted_expiry="orphan-expiry",
                purpose="migration-test", created_by_id=orphan_user.pk,
            )
            Grant.objects.using(alias).create(
                token_hash="migration-orphan-token-hash", field_name="pan", reason="Motivo huérfano",
                internal_reference="ORPHAN-001", session_key="orphan-session",
                expires_at="2099-01-01T00:00:00Z", card_id=orphan_card.pk, user_id=orphan_user.pk,
            )
            executor = MigrationExecutor(connection)
            executor.migrate(target_0009)
            apps_0009 = executor.loader.project_state(target_0009).apps
            Context = apps_0009.get_model("vault", "ProtectedOperationContext")
            GrantFinal = apps_0009.get_model("vault", "RevealGrant")
            WindowFinal = apps_0009.get_model("vault", "SensitiveOperationWindow")
            contexts = list(Context.objects.using(alias).order_by("id"))
            self.assertEqual(len(contexts), 3)
            granted_context = Context.objects.using(alias).get(session_hash="session-with-grant")
            blank_context = Context.objects.using(alias).get(session_hash="session-without-grant")
            orphan_context = Context.objects.using(alias).get(session_hash="orphan-session")
            self.assertEqual(granted_context.reason, "Motivo grant")
            self.assertEqual(granted_context.internal_reference, "GRANT-001")
            self.assertEqual(blank_context.reason, "")
            self.assertEqual(blank_context.internal_reference, "")
            self.assertEqual(orphan_context.reason, "Motivo huérfano")
            self.assertEqual(orphan_context.internal_reference, "ORPHAN-001")
            self.assertEqual(GrantFinal.objects.using(alias).get(token_hash="migration-token-hash").operation_context_id, granted_context.pk)
            orphan_grant = GrantFinal.objects.using(alias).get(token_hash="migration-orphan-token-hash")
            self.assertEqual(orphan_grant.operation_window_id, orphan_context.identity_window_id)
            self.assertEqual(orphan_grant.operation_context_id, orphan_context.pk)
            self.assertNotIn("reason", [field.name for field in WindowFinal._meta.get_fields()])
            self.assertNotIn("internal_reference", [field.name for field in WindowFinal._meta.get_fields()])
        finally:
            executor = MigrationExecutor(connection)
            connection.close()
            connections._connections.default = original_connection
            os.unlink(temporary_database.name)
