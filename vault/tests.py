from django.test import TestCase,Client
from django.contrib.auth import get_user_model
from django.urls import reverse
from .models import UserProfile,PaymentCard,AuditEvent
class VaultTests(TestCase):
    def setUp(self):
        U=get_user_model(); self.leader=U.objects.create_user('leader',password='LongPassword123!'); self.leader.vault_profile.role=UserProfile.LEADER; self.leader.vault_profile.save()
        self.analyst=U.objects.create_user('analyst',password='LongPassword123!'); self.analyst.vault_profile.role=UserProfile.ANALYST; self.analyst.vault_profile.save()
        self.card=PaymentCard(client_name='Cliente',cardholder_name='Titular',brand='VISA',purpose='Prueba',created_by=self.leader); self.card.set_pan('4111111111111111'); self.card.set_expiry('12/29'); self.card.save()
    def test_pan_encrypted(self): self.assertNotIn('4111111111111111',self.card.encrypted_pan); self.assertEqual(self.card.get_pan(),'4111111111111111')
    def test_analyst_cannot_create(self): self.client.force_login(self.analyst); self.assertEqual(self.client.get(reverse('vault:card_create')).status_code,403)
    def test_reveal_requires_password_reason(self): self.client.force_login(self.analyst); r=self.client.post(reverse('vault:reveal',args=[self.card.pk]),{'field':'pan','reason':'Pago prueba','password':'LongPassword123!'}); self.assertEqual(r.status_code,200); self.assertTrue(AuditEvent.objects.filter(action='REVEAL').exists())
