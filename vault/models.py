from django.conf import settings
from django.db import models
from .crypto import encrypt,decrypt
class UserProfile(models.Model):
    ADMIN='ADMIN'; LEADER='LEADER'; ANALYST='ANALYST'; ROLES=[(ADMIN,'Administrador'),(LEADER,'Líder de cartera'),(ANALYST,'Analista')]
    user=models.OneToOneField(settings.AUTH_USER_MODEL,on_delete=models.CASCADE,related_name='vault_profile')
    role=models.CharField(max_length=10,choices=ROLES,default=ANALYST)
    active=models.BooleanField(default=True)
    def __str__(self): return f'{self.user} - {self.get_role_display()}'
    @property
    def can_manage_cards(self): return self.role==self.LEADER
    @property
    def can_view_cards(self): return self.role in {self.LEADER,self.ANALYST}
class PaymentCard(models.Model):
    BRAND=[('VISA','Visa'),('MC','Mastercard'),('AMEX','American Express')]
    client_name=models.CharField(max_length=140)
    cardholder_name=models.CharField(max_length=140)
    brand=models.CharField(max_length=10,choices=BRAND)
    encrypted_pan=models.TextField(editable=False)
    last4=models.CharField(max_length=4,editable=False)
    encrypted_expiry=models.TextField(editable=False)
    purpose=models.CharField(max_length=200)
    active=models.BooleanField(default=True)
    created_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name='cards_created')
    created_at=models.DateTimeField(auto_now_add=True); updated_at=models.DateTimeField(auto_now=True)
    def set_pan(self,v):
        digits=''.join(c for c in v if c.isdigit())
        if len(digits)<13 or len(digits)>19: raise ValueError('Número de tarjeta inválido.')
        self.encrypted_pan=encrypt(digits); self.last4=digits[-4:]
    def get_pan(self): return decrypt(self.encrypted_pan)
    def set_expiry(self,v): self.encrypted_expiry=encrypt(v)
    def get_expiry(self): return decrypt(self.encrypted_expiry)
    @property
    def masked_pan(self): return f'•••• •••• •••• {self.last4}'
    def __str__(self): return f'{self.client_name} - {self.last4}'
class AuditEvent(models.Model):
    ACTIONS=[('LOGIN','Inicio de sesión'),('LOGOUT','Cierre de sesión'),('ACCESS','Acceso'),('VIEW','Consulta tarjeta'),('REVEAL','Revelado'),('COPY','Copia'),('CREATE','Creación'),('UPDATE','Actualización'),('DEACTIVATE','Desactivación'),('DENIED','Acceso denegado')]
    user=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,blank=True,on_delete=models.SET_NULL)
    action=models.CharField(max_length=20,choices=ACTIONS); card=models.ForeignKey(PaymentCard,null=True,blank=True,on_delete=models.SET_NULL)
    field_name=models.CharField(max_length=40,blank=True); reason=models.CharField(max_length=240,blank=True)
    ip_address=models.GenericIPAddressField(null=True,blank=True); user_agent=models.CharField(max_length=300,blank=True)
    outside_office_hours=models.BooleanField(default=False); metadata=models.JSONField(default=dict,blank=True); created_at=models.DateTimeField(auto_now_add=True)
    class Meta: ordering=['-created_at']
class SecurityAlert(models.Model):
    event=models.OneToOneField(AuditEvent,on_delete=models.CASCADE); status=models.CharField(max_length=20,default='NEW'); created_at=models.DateTimeField(auto_now_add=True); reviewed_at=models.DateTimeField(null=True,blank=True); review_note=models.TextField(blank=True)
