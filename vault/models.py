from django.conf import settings
from django.db import models

from .crypto import decrypt, encrypt, fingerprint


class UserProfile(models.Model):
    ADMIN = "ADMIN"
    LEADER = "LEADER"
    ANALYST = "ANALYST"
    ROLES = [(ADMIN, "Administrador"), (LEADER, "Líder de cartera"), (ANALYST, "Analista")]
    MFA_NOT_CONFIGURED = "NOT_CONFIGURED"
    MFA_PENDING = "PENDING"
    MFA_ACTIVE = "ACTIVE"
    MFA_BLOCKED = "BLOCKED"
    MFA_RECOVERY = "RECOVERY"
    MFA_STATES = [
        (MFA_NOT_CONFIGURED, "No configurado"), (MFA_PENDING, "Pendiente de enrolamiento"),
        (MFA_ACTIVE, "Activo"), (MFA_BLOCKED, "Bloqueado"), (MFA_RECOVERY, "Recuperación requerida"),
    ]

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="vault_profile")
    role = models.CharField(max_length=10, choices=ROLES, blank=True, default="")
    active = models.BooleanField(default=False)
    mfa_enabled = models.BooleanField(default=False)
    mfa_status = models.CharField(max_length=24, choices=MFA_STATES, default=MFA_NOT_CONFIGURED)
    mfa_failed_attempts = models.PositiveSmallIntegerField(default=0)
    mfa_changed_at = models.DateTimeField(null=True, blank=True)
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
        ("PASSWORD_OK", "Contraseña correcta"), ("MFA_REQUIRED", "MFA requerido"),
        ("MFA_SUCCESS", "MFA exitoso"), ("MFA_FAILED", "MFA fallido"),
        ("MFA_ENROLL_START", "Enrolamiento MFA iniciado"), ("MFA_ENROLL_COMPLETE", "Enrolamiento MFA completado"),
        ("MFA_RECOVERY_USED", "Recuperación MFA utilizada"), ("MFA_RECOVERY_REGENERATED", "Códigos regenerados"),
        ("MFA_RESET", "MFA reiniciado"), ("SESSION_CREATED", "Sesión creada"),
        ("SESSION_REVOKED", "Sesión revocada"), ("SESSION_EXPIRED", "Sesión expirada"),
        ("SESSION_REPLACED", "Sesión reemplazada"), ("DEVICE_NEW", "Dispositivo nuevo"),
        ("DEVICE_TRUSTED", "Dispositivo reconocido"), ("DEVICE_BLOCKED", "Dispositivo bloqueado"),
        ("DEVICE_UNBLOCKED", "Dispositivo desbloqueado"), ("REAUTH_SUCCESS", "Reautenticación exitosa"),
        ("REAUTH_FAILED", "Reautenticación fallida"), ("PASSWORD_CHANGED", "Contraseña cambiada"),
        ("ALERT_CREATED", "Alerta creada"), ("ALERT_REVIEWED", "Alerta revisada"),
        ("ALERT_CLOSED", "Alerta cerrada"), ("CRITICAL_BLOCKED", "Operación crítica bloqueada"),
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
    session_key = models.CharField(max_length=64, blank=True, editable=False)
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
    session_key = models.CharField(max_length=64, editable=False)
    expires_at = models.DateTimeField()
    copied_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class MFARecoveryCode(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="vault_recovery_codes")
    code_hash = models.CharField(max_length=256, editable=False)
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class UserDevice(models.Model):
    NEW = "NEW"; TRUSTED = "TRUSTED"; BLOCKED = "BLOCKED"; REVOKED = "REVOKED"
    STATES = [(NEW, "Nuevo"), (TRUSTED, "Reconocido"), (BLOCKED, "Bloqueado"), (REVOKED, "Revocado")]
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="vault_devices")
    fingerprint_hash = models.CharField(max_length=64, editable=False)
    user_agent = models.CharField(max_length=300, blank=True)
    browser = models.CharField(max_length=80, blank=True)
    operating_system = models.CharField(max_length=80, blank=True)
    device_type = models.CharField(max_length=40, blank=True)
    friendly_name = models.CharField(max_length=100, blank=True)
    initial_ip = models.GenericIPAddressField(null=True, blank=True)
    last_ip = models.GenericIPAddressField(null=True, blank=True)
    first_seen_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(auto_now=True)
    status = models.CharField(max_length=12, choices=STATES, default=NEW)
    trusted_until = models.DateTimeField(null=True, blank=True)
    blocked_at = models.DateTimeField(null=True, blank=True)
    blocked_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="vault_devices_blocked")
    block_reason = models.CharField(max_length=240, blank=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "fingerprint_hash"], name="vault_unique_user_device")]
        indexes = [models.Index(fields=["user", "status"], name="vault_device_user_status")]


class SecureSession(models.Model):
    ACTIVE = "ACTIVE"; REVOKED = "REVOKED"; EXPIRED = "EXPIRED"
    STATES = [(ACTIVE, "Activa"), (REVOKED, "Revocada"), (EXPIRED, "Expirada")]
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="vault_sessions")
    session_hash = models.CharField(max_length=64, unique=True, editable=False)
    encrypted_session_key = models.TextField(editable=False)
    device = models.ForeignKey(UserDevice, null=True, blank=True, on_delete=models.PROTECT, related_name="sessions")
    created_at = models.DateTimeField(auto_now_add=True)
    last_activity_at = models.DateTimeField()
    expires_at = models.DateTimeField()
    initial_ip = models.GenericIPAddressField(null=True, blank=True)
    last_ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=300, blank=True)
    browser = models.CharField(max_length=80, blank=True)
    operating_system = models.CharField(max_length=80, blank=True)
    device_type = models.CharField(max_length=40, blank=True)
    friendly_name = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=12, choices=STATES, default=ACTIVE)
    revocation_reason = models.CharField(max_length=240, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="vault_sessions_revoked")
    mfa_completed = models.BooleanField(default=False)
    mfa_completed_at = models.DateTimeField(null=True, blank=True)
    last_reauthenticated_at = models.DateTimeField(null=True, blank=True)
    new_device = models.BooleanField(default=False)
    outside_office_hours = models.BooleanField(default=False)
    trust_level = models.CharField(max_length=12, default="LOW")
    class Meta:
        indexes = [models.Index(fields=["user", "status"], name="vault_session_user_status")]


class ReauthenticationGrant(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="vault_reauth_grants")
    session_hash = models.CharField(max_length=64, editable=False)
    purpose = models.CharField(max_length=40)
    validated_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    invalidated_at = models.DateTimeField(null=True, blank=True)
    class Meta:
        indexes = [models.Index(fields=["user", "session_hash", "purpose", "expires_at"], name="vault_reauth_lookup")]


class SecurityAlert(models.Model):
    STATUSES = [("NEW", "Nueva"), ("REVIEWED", "Revisada"), ("JUSTIFIED", "Justificada"), ("ESCALATED", "Escalada"), ("CLOSED", "Cerrada")]
    SEVERITIES = [("LOW", "Baja"), ("MEDIUM", "Media"), ("HIGH", "Alta"), ("CRITICAL", "Crítica")]
    event = models.OneToOneField(AuditEvent, on_delete=models.PROTECT)
    alert_type = models.CharField(max_length=40, default="SECURITY_EVENT")
    severity = models.CharField(max_length=10, choices=SEVERITIES, default="MEDIUM")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="vault_alerts_acted")
    affected_user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="vault_alerts_received")
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    device = models.ForeignKey(UserDevice, null=True, blank=True, on_delete=models.SET_NULL, related_name="alerts")
    description = models.CharField(max_length=240, blank=True)
    safe_metadata = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=20, choices=STATUSES, default="NEW")
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_note = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="vault_alerts_reviewed")
    closed_at = models.DateTimeField(null=True, blank=True)
