from django.conf import settings
from django.db import models

from .crypto import decrypt, encrypt, fingerprint


class UserProfile(models.Model):
    ADMIN = "ADMIN"
    LEADER = "LEADER"
    ANALYST = "ANALYST"
    ROLES = [(ADMIN, "Administrador"), (LEADER, "Líder de cartera"), (ANALYST, "Analista")]

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="vault_profile")
    role = models.CharField(max_length=10, choices=ROLES, blank=True, default="")
    active = models.BooleanField(default=False)
    mfa_enabled = models.BooleanField(default=False)
    password_changed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.user} - {self.get_role_display() or 'Sin rol'}"

    @property
    def can_manage_cards(self):
        return self.active and self.role == self.LEADER

    @property
    def can_view_cards(self):
        return self.active and self.role in {self.LEADER, self.ANALYST}


class PaymentCard(models.Model):
    BRAND = [("VISA", "Visa"), ("MC", "Mastercard"), ("AMEX", "American Express")]
    client_name = models.CharField(max_length=140)
    cardholder_name = models.CharField(max_length=140)
    brand = models.CharField(max_length=10, choices=BRAND)
    encrypted_pan = models.TextField(editable=False)
    pan_fingerprint = models.CharField(max_length=64, editable=False, unique=True)
    last4 = models.CharField(max_length=4, editable=False, db_index=True)
    encrypted_expiry = models.TextField(editable=False)
    purpose = models.CharField(max_length=200)
    active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="cards_created")
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="cards_updated")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def set_pan(self, value):
        digits = "".join(character for character in value if character.isdigit())
        if len(digits) < 13 or len(digits) > 19:
            raise ValueError("Número de tarjeta inválido.")
        self.encrypted_pan = encrypt(digits)
        self.pan_fingerprint = fingerprint(digits)
        self.last4 = digits[-4:]

    def get_pan(self):
        return decrypt(self.encrypted_pan)

    def set_expiry(self, value):
        self.encrypted_expiry = encrypt(value)

    def get_expiry(self):
        return decrypt(self.encrypted_expiry)

    @property
    def masked_pan(self):
        return f"•••• •••• •••• {self.last4}"

    def __str__(self):
        return f"{self.client_name} - {self.last4}"


class AuditEvent(models.Model):
    ACTIONS = [
        ("LOGIN", "Inicio de sesión"), ("LOGIN_FAILED", "Inicio fallido"), ("LOGOUT", "Cierre de sesión"),
        ("ACCESS", "Acceso"), ("VIEW", "Consulta tarjeta"), ("REVEAL", "Revelado"),
        ("COPY", "Copia"), ("COPY_ATTEMPT", "Intento de copia"), ("CREATE", "Creación"),
        ("UPDATE", "Actualización"), ("DEACTIVATE", "Desactivación"), ("DENIED", "Acceso denegado"),
        ("INTEGRITY_FAILURE", "Fallo de integridad"),
    ]
    RISK_LEVELS = [("LOW", "Bajo"), ("MEDIUM", "Medio"), ("HIGH", "Alto"), ("CRITICAL", "Crítico")]

    sequence = models.PositiveBigIntegerField(unique=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    actor_role = models.CharField(max_length=10, blank=True)
    action = models.CharField(max_length=24, choices=ACTIONS)
    card = models.ForeignKey(PaymentCard, null=True, blank=True, on_delete=models.SET_NULL)
    field_name = models.CharField(max_length=40, blank=True)
    reason = models.CharField(max_length=240, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=300, blank=True)
    session_key = models.CharField(max_length=40, blank=True)
    path = models.CharField(max_length=300, blank=True)
    method = models.CharField(max_length=10, blank=True)
    result = models.CharField(max_length=20, default="SUCCESS")
    risk_level = models.CharField(max_length=10, choices=RISK_LEVELS, default="LOW")
    outside_office_hours = models.BooleanField(default=False)
    metadata = models.JSONField(default=dict, blank=True)
    previous_hash = models.CharField(max_length=64, blank=True)
    event_hash = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-sequence"]


class AuditChainState(models.Model):
    singleton = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    last_sequence = models.PositiveBigIntegerField(default=0)
    last_hash = models.CharField(max_length=64, blank=True)


class RevealGrant(models.Model):
    token_hash = models.CharField(max_length=64, unique=True, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    card = models.ForeignKey(PaymentCard, on_delete=models.CASCADE)
    field_name = models.CharField(max_length=20)
    reason = models.CharField(max_length=240)
    session_key = models.CharField(max_length=40)
    expires_at = models.DateTimeField()
    copied_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class SecurityAlert(models.Model):
    STATUSES = [("NEW", "Nueva"), ("REVIEWED", "Revisada"), ("JUSTIFIED", "Justificada"), ("ESCALATED", "Escalada"), ("CLOSED", "Cerrada")]
    event = models.OneToOneField(AuditEvent, on_delete=models.PROTECT)
    status = models.CharField(max_length=20, choices=STATUSES, default="NEW")
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_note = models.TextField(blank=True)
