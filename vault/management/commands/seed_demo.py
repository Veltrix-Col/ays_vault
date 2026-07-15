from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from vault.models import UserProfile,PaymentCard
class Command(BaseCommand):
    help='Crea usuarios y 30 tarjetas ficticias.'
    def handle(self,*args,**opts):
        U=get_user_model(); users={}
        for username,role,name in [('adminvault',UserProfile.ADMIN,'Administrador Seguridad'),('lidercartera',UserProfile.LEADER,'Laura Líder'),('analistacartera',UserProfile.ANALYST,'Andrés Analista')]:
            u,created=U.objects.get_or_create(username=username,defaults={'first_name':name,'email':f'{username}@example.com'})
            u.set_password('DemoSeguro2026!'); u.is_staff=(role==UserProfile.ADMIN); u.is_superuser=(role==UserProfile.ADMIN); u.save(); u.vault_profile.role=role; u.vault_profile.save(); users[role]=u
        brands=['VISA','MC','AMEX']; prefixes={'VISA':'411111111111','MC':'555555555555','AMEX':'37828224631'}
        for i in range(1,31):
            brand=brands[(i-1)%3]; pan=(prefixes[brand]+f'{i:04d}')[:15 if brand=='AMEX' else 16]
            if not PaymentCard.objects.filter(client_name=f'Cliente Demo {i:02d}').exists():
                c=PaymentCard(client_name=f'Cliente Demo {i:02d}',cardholder_name=f'Titular Prueba {i:02d}',brand=brand,purpose='Pago de obligaciones autorizadas - dato completamente ficticio',created_by=users[UserProfile.LEADER]); c.set_pan(pan); c.set_expiry(f'{(i%12)+1:02d}/{27+(i%4):02d}'); c.save()
        self.stdout.write(self.style.SUCCESS('Demo creada. Usuarios: adminvault, lidercartera, analistacartera / clave DemoSeguro2026!'))
