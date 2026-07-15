from django.conf import settings
from django.db import models

from .crypto import decrypt, encrypt, fingerprint


class PolicyConfiguration(models.Model):
    """Configuracion operativa central. Solo puede existir la fila singleton=1."""

    SESSION_POLICIES = [("REVOKE_PREVIOUS", "Revocar anterior"), ("BLOCK_NEW", "Bloquear nueva"), ("ALLOW_LIMIT", "Permitir segun limite")]
    OUTSIDE_BEHAVIORS = [("ALLOW", "Permitir"), ("ALLOW_ALERT", "Permitir y alertar"), ("REAUTH", "Exigir reautenticacion"), ("BLOCK", "Bloquear")]

    singleton = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    timezone_name = models.CharField(max_length=64, default="America/Bogota")
    weekday_start = models.TimeField(default="07:00")
    weekday_end = models.TimeField(default="18:00")
    saturday_enabled = models.BooleanField(default=False)
    saturday_start = models.TimeField(default="08:00")
    saturday_end = models.TimeField(default="12:00")
    sunday_enabled = models.BooleanField(default=False)
    session_inactivity_minutes = models.PositiveSmallIntegerField(default=10)
    maximum_sessions = models.PositiveSmallIntegerField(default=1)
    new_session_policy = models.CharField(max_length=24, choices=SESSION_POLICIES, default="REVOKE_PREVIOUS")
    reauthentication_minutes = models.PositiveSmallIntegerField(default=5)
    reauthentication_operations = models.JSONField(default=list, blank=True)
    outside_hours_behavior = models.CharField(max_length=20, choices=OUTSIDE_BEHAVIORS, default="ALLOW_ALERT")
    inactivity_login_days = models.PositiveSmallIntegerField(default=30)
    inactivity_reveal_days = models.PositiveSmallIntegerField(default=30)
    inactivity_copy_days = models.PositiveSmallIntegerField(default=30)
    inactivity_general_days = models.PositiveSmallIntegerField(default=30)
    inactive_user_days = models.PositiveSmallIntegerField(default=30)
    operational_user_days = models.PositiveSmallIntegerField(default=45)
    alert_review_hours = models.PositiveSmallIntegerField(default=24)
    escalation_hours = models.PositiveSmallIntegerField(default=48)
    enabled = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="vault_policies_updated")

    def save(self, *args, **kwargs):
        self.singleton = 1
        return super().save(*args, **kwargs)


class Holiday(models.Model):
    date = models.DateField(unique=True)
    name = models.CharField(max_length=140)
    national = models.BooleanField(default=True)
    internal = models.BooleanField(default=False)
    working_day = models.BooleanField(default=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="vault_holidays_created")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["date"]
        indexes = [models.Index(fields=["date", "working_day"], name="vault_holiday_lookup")]


class AccessException(models.Model):
    ALLOW = "ALLOW"; BLOCK = "BLOCK"; EXTEND = "EXTEND"; VACATION = "VACATION"
    TYPES = [(ALLOW, "Permitir"), (BLOCK, "Bloquear"), (EXTEND, "Ampliar horario"), (VACATION, "Vacaciones o pausa operativa")]
    ACTIVE = "ACTIVE"; REVOKED = "REVOKED"; EXPIRED = "EXPIRED"
    STATUSES = [(ACTIVE, "Activa"), (REVOKED, "Revocada"), (EXPIRED, "Expirada")]

    name = models.CharField(max_length=140)
    exception_type = models.CharField(max_length=16, choices=TYPES)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE, related_name="vault_access_exceptions")
    role = models.CharField(max_length=10, blank=True, choices=[("ADMIN", "Administrador"), ("LEADER", "Lider de cartera"), ("ANALYST", "Analista")])
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    daily_start = models.TimeField(null=True, blank=True)
    daily_end = models.TimeField(null=True, blank=True)
    operations = models.JSONField(default=list, blank=True)
    reason = models.CharField(max_length=240)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="vault_exceptions_created")
    created_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=12, choices=STATUSES, default=ACTIVE)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="vault_exceptions_revoked")
    revocation_reason = models.CharField(max_length=240, blank=True)

    class Meta:
        ordering = ["-starts_at"]
        constraints = [models.CheckConstraint(condition=models.Q(ends_at__gt=models.F("starts_at")), name="vault_exception_valid_period")]
        indexes = [models.Index(fields=["status", "starts_at", "ends_at"], name="vault_exception_window")]


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
        ("ALERT_CLOSED", "Alerta cerrada"), ("ALERT_ESCALATED", "Alerta escalada"),
        ("ALERT_REOPENED", "Alerta reabierta"), ("POLICY_CHANGED", "Politica modificada"),
        ("EXCEPTION_CREATED", "Excepcion creada"), ("EXCEPTION_REVOKED", "Excepcion revocada"),
        ("EMAIL_SENT", "Correo enviado"), ("EMAIL_FAILED", "Fallo de correo"),
        ("POLICY_EVALUATION", "Evaluacion programada"), ("CRITICAL_BLOCKED", "Operación crítica bloqueada"),
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
    STATUSES = [("NEW", "Nueva"), ("IN_REVIEW", "En revision"), ("REVIEWED", "Revisada"), ("JUSTIFIED", "Justificada"), ("ESCALATED", "Escalada"), ("CLOSED", "Cerrada"), ("REOPENED", "Reabierta")]
    event = models.OneToOneField(AuditEvent, on_delete=models.PROTECT)
    alert_type = models.CharField(max_length=40, default="SECURITY_EVENT")
    severity = models.CharField(max_length=10, choices=SEVERITIES, default="MEDIUM")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="vault_alerts_acted")
    affected_user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="vault_alerts_received")
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    device = models.ForeignKey(UserDevice, null=True, blank=True, on_delete=models.SET_NULL, related_name="alerts")
    policy = models.ForeignKey(PolicyConfiguration, null=True, blank=True, on_delete=models.PROTECT, related_name="alerts")
    access_exception = models.ForeignKey(AccessException, null=True, blank=True, on_delete=models.PROTECT, related_name="alerts")
    description = models.CharField(max_length=240, blank=True)
    safe_metadata = models.JSONField(default=dict, blank=True)
    recommendation = models.CharField(max_length=300, blank=True)
    due_at = models.DateTimeField(null=True, blank=True)
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="vault_alerts_assigned")
    escalated_to = models.CharField(max_length=200, blank=True)
    idempotency_key = models.CharField(max_length=64, unique=True, null=True, blank=True, editable=False)
    status = models.CharField(max_length=20, choices=STATUSES, default="NEW")
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_note = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="vault_alerts_reviewed")
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["status", "severity", "due_at"], name="vault_alert_queue"),
            models.Index(fields=["alert_type", "created_at"], name="vault_alert_type_date"),
        ]


class AlertTransition(models.Model):
    alert = models.ForeignKey(SecurityAlert, on_delete=models.PROTECT, related_name="transitions")
    from_status = models.CharField(max_length=20, blank=True)
    to_status = models.CharField(max_length=20)
    action = models.CharField(max_length=20)
    comment = models.TextField(blank=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="vault_alert_transitions")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class NotificationRecipient(models.Model):
    IMMEDIATE = "IMMEDIATE"; DAILY = "DAILY"; WEEKLY = "WEEKLY"
    MODES = [(IMMEDIATE, "Inmediato"), (DAILY, "Resumen diario"), (WEEKLY, "Resumen semanal")]
    name = models.CharField(max_length=120)
    email = models.EmailField()
    alert_types = models.JSONField(default=list, blank=True)
    minimum_severity = models.CharField(max_length=10, choices=SecurityAlert.SEVERITIES, default="HIGH")
    send_start = models.TimeField(null=True, blank=True)
    send_end = models.TimeField(null=True, blank=True)
    delivery_mode = models.CharField(max_length=12, choices=MODES, default=IMMEDIATE)
    active = models.BooleanField(default=True)
    is_primary = models.BooleanField(default=False)
    is_leader = models.BooleanField(default=False)
    is_alternate = models.BooleanField(default=False)
    is_escalation = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="vault_recipients_updated")


class NotificationRecord(models.Model):
    PENDING = "PENDING"; SENT = "SENT"; FAILED = "FAILED"; RETRY = "RETRY"
    RESULTS = [(PENDING, "Pendiente"), (SENT, "Enviado"), (FAILED, "Fallido"), (RETRY, "Reintento")]
    alert = models.ForeignKey(SecurityAlert, null=True, blank=True, on_delete=models.PROTECT, related_name="notifications")
    notification_type = models.CharField(max_length=60)
    masked_recipient = models.CharField(max_length=254)
    recipient_hash = models.CharField(max_length=64, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    result = models.CharField(max_length=12, choices=RESULTS, default=PENDING)
    safe_error_code = models.CharField(max_length=80, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    backend = models.CharField(max_length=60)
    external_id = models.CharField(max_length=160, blank=True)
    idempotency_hash = models.CharField(max_length=64, unique=True, editable=False)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["result", "next_attempt_at"], name="vault_notify_retry")]


class PolicyEvaluationRun(models.Model):
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    dry_run = models.BooleanField(default=False)
    status = models.CharField(max_length=20, default="RUNNING")
    checks = models.JSONField(default=dict, blank=True)
    safe_error = models.CharField(max_length=160, blank=True)


class AuditVerificationRun(models.Model):
    checked_at = models.DateTimeField(auto_now_add=True)
    valid = models.BooleanField()
    position = models.PositiveBigIntegerField(default=0)
    source = models.CharField(max_length=40, default="COMMAND")
