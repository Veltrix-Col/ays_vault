from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .forms import CardForm, luhn_valid
from .models import AuditEvent, PaymentCard, RevealGrant, UserProfile
from .security import verify_audit_chain


PASSWORD = "LongPassword123!"
PAN = "4111111111111111"


@override_settings(APP_ENV="development", FIELD_ENCRYPTION_KEY="MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=", FIELD_FINGERPRINT_KEY="test-only-fingerprint-key")
class VaultSecurityTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin_user = User.objects.create_user("admin.persona", password=PASSWORD)
        self.leader = User.objects.create_user("lider.persona", password=PASSWORD)
        self.analyst = User.objects.create_user("analista.persona", password=PASSWORD)
        self.unassigned = User.objects.create_user("pendiente.persona", password=PASSWORD)
        for user, role in ((self.admin_user, UserProfile.ADMIN), (self.leader, UserProfile.LEADER), (self.analyst, UserProfile.ANALYST)):
            user.vault_profile.role = role
            user.vault_profile.active = True
            user.vault_profile.save()
        self.card = PaymentCard(client_name="Cliente Demo", cardholder_name="Titular Demo", brand="VISA", purpose="Prueba autorizada", created_by=self.leader, updated_by=self.leader)
        self.card.set_pan(PAN)
        self.card.set_expiry("12/29")
        self.card.save()

    def login(self, user):
        self.client.force_login(user)

    def reveal(self, user=None, **overrides):
        self.login(user or self.analyst)
        data = {"field": "pan", "reason": "Pago factura demo", "reference": "FAC-001", "password": PASSWORD}
        data.update(overrides)
        return self.client.post(reverse("vault:reveal", args=[self.card.pk]), data)

    def test_pan_and_expiry_are_encrypted_at_rest(self):
        stored = PaymentCard.objects.get(pk=self.card.pk)
        self.assertNotIn(PAN, stored.encrypted_pan)
        self.assertNotIn("12/29", stored.encrypted_expiry)
        self.assertEqual(stored.get_pan(), PAN)
        self.assertEqual(stored.last4, "1111")

    def test_luhn_and_duplicate_validation(self):
        self.assertTrue(luhn_valid(PAN))
        form = CardForm(data={"client_name": "Otro", "cardholder_name": "Demo", "brand": "VISA", "purpose": "Demo", "active": True, "pan": PAN, "expiry": "10/30"})
        self.assertFalse(form.is_valid())
        self.assertIn("ya está registrada", str(form.errors))

    def test_role_matrix_for_card_list(self):
        url = reverse("vault:card_list")
        for user, expected in ((self.admin_user, 403), (self.leader, 200), (self.analyst, 200), (self.unassigned, 403)):
            self.login(user)
            self.assertEqual(self.client.get(url).status_code, expected)
            self.client.logout()

    def test_only_leader_can_create(self):
        url = reverse("vault:card_create")
        for user, expected in ((self.admin_user, 403), (self.leader, 200), (self.analyst, 403), (self.unassigned, 403)):
            self.login(user)
            self.assertEqual(self.client.get(url).status_code, expected)
            self.client.logout()

    def test_admin_cannot_detail_reveal_or_copy(self):
        self.login(self.admin_user)
        self.assertEqual(self.client.get(reverse("vault:card_detail", args=[self.card.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse("vault:reveal", args=[self.card.pk]), {"field": "pan", "reason": "No autorizado", "password": PASSWORD}).status_code, 403)
        self.assertEqual(self.client.post(reverse("vault:copy_event", args=[self.card.pk]), {"copy_token": "x", "result": "success"}).status_code, 403)

    def test_edit_form_does_not_preload_sensitive_values(self):
        self.login(self.leader)
        response = self.client.get(reverse("vault:card_edit", args=[self.card.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, PAN)
        self.assertNotContains(response, "12/29")

    def test_reveal_requires_reason_and_correct_password(self):
        self.assertEqual(self.reveal(reason="").status_code, 400)
        self.assertEqual(self.reveal(password="incorrecta").status_code, 400)
        self.assertEqual(self.reveal().status_code, 200)

    def test_reveal_returns_only_requested_field_and_no_store(self):
        response = self.reveal(field="expiry")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.json()), {"ok", "field", "value", "copy_token", "expires_in"})
        self.assertEqual(response.json()["value"], "12/29")
        self.assertIn("no-store", response.headers["Cache-Control"])

    def test_copy_requires_live_one_time_reveal_grant(self):
        self.login(self.analyst)
        url = reverse("vault:copy_event", args=[self.card.pk])
        denied = self.client.post(url, {"copy_token": "forged", "result": "success"})
        self.assertEqual(denied.status_code, 403)
        reveal = self.client.post(reverse("vault:reveal", args=[self.card.pk]), {"field": "pan", "reason": "Pago demo", "password": PASSWORD}).json()
        copied = self.client.post(url, {"copy_token": reveal["copy_token"], "result": "success"})
        self.assertEqual(copied.status_code, 200)
        self.assertEqual(self.client.post(url, {"copy_token": reveal["copy_token"], "result": "success"}).status_code, 403)
        event = AuditEvent.objects.filter(action="COPY").latest("sequence")
        self.assertEqual(event.field_name, "pan")
        self.assertNotIn(PAN, str(event.metadata) + event.reason)

    def test_expired_reveal_grant_cannot_copy(self):
        response = self.reveal()
        token = response.json()["copy_token"]
        RevealGrant.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.client.post(reverse("vault:copy_event", args=[self.card.pk]), {"copy_token": token, "result": "success"}).status_code, 403)

    def test_analyst_cannot_access_inactive_card_by_idor(self):
        self.card.active = False
        self.card.save(update_fields=["active"])
        self.login(self.analyst)
        self.assertEqual(self.client.get(reverse("vault:card_detail", args=[self.card.pk])).status_code, 404)

    def test_critical_endpoints_require_post_and_csrf(self):
        self.login(self.leader)
        self.assertEqual(self.client.get(reverse("vault:reveal", args=[self.card.pk])).status_code, 405)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.leader)
        self.assertEqual(csrf_client.post(reverse("vault:reveal", args=[self.card.pk]), {"field": "pan", "reason": "Pago", "password": PASSWORD}).status_code, 403)

    def test_audit_chain_verifies_and_detects_tampering(self):
        self.reveal()
        valid, _ = verify_audit_chain()
        self.assertTrue(valid)
        event = AuditEvent.objects.filter(action="REVEAL").latest("sequence")
        AuditEvent.objects.filter(pk=event.pk).update(reason="alterado")
        valid, position = verify_audit_chain()
        self.assertFalse(valid)
        self.assertEqual(position, event.sequence)

    def test_verify_command_succeeds_for_intact_chain(self):
        self.reveal()
        call_command("verify_audit_chain", verbosity=0)
