from __future__ import annotations

import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class WorkspacePolizaColectivo(models.Model):
    """Workspace local cifrado; Zoho sigue siendo la fuente de verdad."""

    workspace_key = models.CharField(max_length=64, unique=True, editable=False)
    profile = models.CharField(max_length=12, db_index=True)
    backend = models.CharField(max_length=12)
    source_kind = models.CharField(
        max_length=12,
        choices=(("company", "Empresa"), ("person", "Individuo")),
    )
    policy_reference_hash = models.CharField(max_length=64, db_index=True, editable=False)
    source_reference_hash = models.CharField(max_length=64, db_index=True, editable=False)
    encrypted_snapshot = models.TextField(editable=False)
    snapshot_checksum = models.CharField(max_length=64, editable=False)
    snapshot_version = models.PositiveSmallIntegerField(default=1)
    revision = models.PositiveIntegerField(default=1)
    record_count = models.PositiveIntegerField(default=0)
    warning_count = models.PositiveIntegerField(default=0)
    safe_metrics = models.JSONField(default=dict, blank=True)
    safe_timeline = models.JSONField(default=list, blank=True)
    synced_at = models.DateTimeField(db_index=True)
    expires_at = models.DateTimeField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = (
            models.Index(
                fields=("profile", "source_kind", "expires_at"),
                name="colect_ws_profile_exp",
            ),
        )


class SolicitudColectivo(models.Model):
    class Status(models.TextChoices):
        DRAFT = "BORRADOR", "Borrador"
        READY = "LISTA_PARA_ENVIAR", "Lista para enviar"
        SENT = "ENVIADA", "Enviada"
        OPENED = "ABIERTA_POR_CLIENTE", "Abierta por cliente"
        ANSWERED = "RESPONDIDA", "Respondida"
        REVIEW = "EN_REVISION", "En revisión"
        CORRECTION = "REQUIERE_CORRECCION", "Requiere corrección"
        APPROVED = "APROBADA", "Aprobada"
        PENDING_ZOHO = "PENDIENTE_ZOHO", "Pendiente Zoho"
        LOADED_ZOHO = "CARGADA_ZOHO", "Cargada Zoho"
        CLOSED = "CERRADA", "Cerrada"
        EXPIRED = "VENCIDA", "Vencida"
        CANCELLED = "CANCELADA", "Cancelada"

    class RequestType(models.TextChoices):
        UPDATE = "ACTUALIZACION", "Actualización de datos"
        RENEWAL = "RENOVACION", "Renovación"
        INCLUSION = "INCLUSION", "Inclusión"
        RETIREMENT = "RETIRO", "Retiro"
        MODIFICATION = "MODIFICACION", "Modificación"
        QUOTE = "COTIZACION", "Cotización"
        OTHER = "OTRO", "Otro"

    class Origin(models.TextChoices):
        INTERNAL = "INTERNO", "Interno"
        WEB = "FORMULARIO_WEB", "Formulario web futuro"
        EXCEL = "EXCEL", "Excel futuro"

    TRANSITIONS = {
        Status.DRAFT: {Status.READY, Status.CANCELLED},
        Status.READY: {Status.SENT, Status.EXPIRED, Status.CANCELLED},
        Status.SENT: {Status.OPENED, Status.EXPIRED, Status.CANCELLED},
        Status.OPENED: {Status.ANSWERED, Status.EXPIRED},
        Status.ANSWERED: {Status.REVIEW},
        Status.REVIEW: {Status.APPROVED, Status.CORRECTION, Status.CANCELLED},
        Status.CORRECTION: {Status.SENT, Status.OPENED, Status.ANSWERED, Status.EXPIRED, Status.CANCELLED},
        Status.APPROVED: {Status.PENDING_ZOHO, Status.CLOSED, Status.CANCELLED},
        Status.PENDING_ZOHO: {Status.LOADED_ZOHO, Status.CLOSED},
        Status.LOADED_ZOHO: {Status.CLOSED},
        Status.CLOSED: set(),
        Status.CANCELLED: set(),
    }

    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    public_id = models.CharField(max_length=24, unique=True, editable=False)
    source_kind = models.CharField(max_length=12, choices=(("company", "Empresa"), ("person", "Individuo")))
    source_reference_hash = models.CharField(max_length=64, db_index=True)
    policy_reference_hash = models.CharField(max_length=64, db_index=True)
    encrypted_policy_token = models.TextField(editable=False)
    masked_policy_reference = models.CharField(max_length=80)
    client_label = models.CharField(max_length=180)
    branch_code = models.CharField(max_length=8, db_index=True)
    branch_name = models.CharField(max_length=100)
    request_type = models.CharField(max_length=20, choices=RequestType.choices, db_index=True)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.DRAFT, db_index=True)
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="solicitudes_colectivos_asignadas")
    deadline = models.DateField(db_index=True)
    zoho_profile = models.CharField(max_length=12)
    snapshot_version = models.PositiveSmallIntegerField(default=1)
    snapshot_revision = models.PositiveIntegerField(default=1)
    encrypted_snapshot = models.TextField(editable=False)
    record_count = models.PositiveIntegerField(default=0)
    warnings = models.JSONField(default=list, blank=True)
    encrypted_internal_notes = models.TextField(blank=True)
    origin = models.CharField(max_length=20, choices=Origin.choices, default=Origin.INTERNAL)
    is_test = models.BooleanField(default=False, db_index=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="solicitudes_colectivos_creadas")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = (
            models.Index(fields=("status", "deadline"), name="colect_status_deadline"),
            models.Index(fields=("assigned_to", "status"), name="colect_owner_status"),
            models.Index(fields=("branch_code", "request_type"), name="colect_branch_type"),
        )
        permissions = (
            ("view_requests", "Puede ver solicitudes de Colectivos"),
            ("create_requests", "Puede crear solicitudes de Colectivos"),
            ("edit_requests", "Puede editar borradores de Colectivos"),
            ("assign_requests", "Puede asignar solicitudes de Colectivos"),
            ("approve_requests", "Puede aprobar solicitudes de Colectivos"),
            ("close_requests", "Puede cerrar solicitudes de Colectivos"),
            ("cancel_requests", "Puede cancelar solicitudes de Colectivos"),
            ("export_excel", "Puede exportar Excel de Colectivos"),
            ("view_economic_data", "Puede ver información económica de Colectivos"),
            ("view_personal_data", "Puede ver datos personales de Colectivos"),
            ("manage_notifications", "Puede gestionar notificaciones de Colectivos"),
            ("generate_external_access", "Puede generar accesos externos"),
            ("send_requests", "Puede enviar solicitudes externas"),
            ("regenerate_external_access", "Puede regenerar accesos externos"),
            ("revoke_external_access", "Puede revocar accesos externos"),
            ("view_responses", "Puede ver respuestas externas"),
            ("review_responses", "Puede revisar respuestas externas"),
            ("request_corrections", "Puede solicitar correcciones"),
            ("approve_responses", "Puede aprobar respuestas"),
            ("download_attachments", "Puede descargar adjuntos"),
            ("import_excel", "Puede importar Excel de novedades"),
            ("export_response", "Puede exportar respuestas"),
            ("export_comparison", "Puede exportar comparativos"),
            ("export_approved", "Puede exportar consolidados aprobados"),
        )

    def save(self, *args, **kwargs):
        if not self.public_id:
            self.public_id = f"COL-{timezone.now():%Y}-{self.uuid.hex[:8].upper()}"
        super().save(*args, **kwargs)

    def transition_to(self, target: str) -> None:
        if target not in self.TRANSITIONS.get(self.status, set()):
            raise ValidationError("La transición solicitada no está permitida.")
        if target == self.Status.APPROVED and not self.assigned_to_id:
            raise ValidationError("La solicitud requiere responsable antes de aprobarse.")
        self.status = target
        if target == self.Status.CLOSED:
            self.closed_at = timezone.now()


class SolicitudColectivoPoliza(models.Model):
    class Modality(models.TextChoices):
        UNKNOWN = "NO_DETERMINADA", "No determinada"
        EMPLOYER = "PATRONAL", "Patronal"
        VOLUNTARY = "VOLUNTARIA", "Voluntaria"
        MIXED = "MIXTA", "Mixta"

    request = models.ForeignKey(SolicitudColectivo, on_delete=models.CASCADE, related_name="policies")
    policy_reference_hash = models.CharField(max_length=64, db_index=True)
    encrypted_policy_token = models.TextField(editable=False)
    masked_policy_reference = models.CharField(max_length=80)
    branch_code = models.CharField(max_length=8, db_index=True)
    branch_name = models.CharField(max_length=100)
    modality = models.CharField(max_length=20, choices=Modality.choices, default=Modality.UNKNOWN)
    insurer = models.CharField(max_length=160, blank=True)
    policy_status = models.CharField(max_length=80, blank=True)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    parameter_version = models.PositiveSmallIntegerField(default=1)
    enabled_adjustments = models.JSONField(default=list)
    encrypted_snapshot = models.TextField(editable=False)
    snapshot_checksum = models.CharField(max_length=64)
    record_count = models.PositiveIntegerField(default=0)
    warnings = models.JSONField(default=list, blank=True)
    position = models.PositiveSmallIntegerField()
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ("position",)
        constraints = (
            models.UniqueConstraint(fields=("request", "position"), name="colect_policy_position"),
            models.UniqueConstraint(fields=("request", "policy_reference_hash"), name="colect_request_policy"),
        )
        indexes = (
            models.Index(fields=("request", "active"), name="colect_policy_active"),
            models.Index(fields=("branch_code", "active"), name="colect_policy_branch"),
        )


class SolicitudColectivoRegistro(models.Model):
    public_key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

    class ElementType(models.TextChoices):
        PERSON = "PERSONA", "Persona"
        BENEFICIARY = "BENEFICIARIO", "Beneficiario"
        PROPERTY = "INMUEBLE", "Inmueble"
        VEHICLE = "VEHICULO", "Vehículo"
        OBLIGATION = "OBLIGACION", "Obligación"
        OTHER = "OTRO", "Otro"

    request = models.ForeignKey(SolicitudColectivo, on_delete=models.CASCADE, related_name="records")
    policy = models.ForeignKey(SolicitudColectivoPoliza, null=True, blank=True, on_delete=models.CASCADE, related_name="records")
    element_type = models.CharField(max_length=20, choices=ElementType.choices)
    role = models.CharField(max_length=40)
    external_reference_hash = models.CharField(max_length=64)
    initial_status = models.CharField(max_length=80, blank=True)
    entry_date = models.DateField(null=True, blank=True)
    exit_date = models.DateField(null=True, blank=True)
    plan = models.CharField(max_length=120, blank=True)
    economic_values = models.JSONField(default=dict, blank=True)
    encrypted_branch_payload = models.TextField(blank=True)
    original_position = models.PositiveIntegerField()
    checksum = models.CharField(max_length=64)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ("original_position",)
        constraints = (
            models.UniqueConstraint(fields=("request", "original_position"), name="colect_request_position_unique"),
        )
        indexes = (models.Index(fields=("request", "element_type"), name="colect_request_element"),)


class EventoSolicitudColectivo(models.Model):
    request = models.ForeignKey(SolicitudColectivo, on_delete=models.CASCADE, related_name="events")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    event_type = models.CharField(max_length=40)
    previous_status = models.CharField(max_length=24, blank=True)
    new_status = models.CharField(max_length=24, blank=True)
    safe_metadata = models.JSONField(default=dict, blank=True)
    correlation_id = models.UUIDField(default=uuid.uuid4, editable=False)
    origin = models.CharField(max_length=20, default="INTERNO")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)


class NotificacionColectivos(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notificaciones_colectivos")
    request = models.ForeignKey(SolicitudColectivo, on_delete=models.CASCADE, related_name="notifications")
    notification_type = models.CharField(max_length=40)
    title = models.CharField(max_length=120)
    message = models.CharField(max_length=240)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    read_at = models.DateTimeField(null=True, blank=True)
    priority = models.CharField(max_length=12, default="NORMAL")
    deduplication_key = models.CharField(max_length=120)

    class Meta:
        ordering = ("-created_at",)
        constraints = (models.UniqueConstraint(fields=("user", "deduplication_key"), name="colect_notification_dedup"),)
        indexes = (models.Index(fields=("user", "read_at"), name="colect_user_unread"),)

    @property
    def is_read(self) -> bool:
        return self.read_at is not None


class AccesoExternoSolicitudColectivo(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "ACTIVO", "Activo"
        VERIFIED = "VERIFICADO", "Verificado"
        USED = "USADO", "Usado"
        EXPIRED = "EXPIRADO", "Expirado"
        REVOKED = "REVOCADO", "Revocado"
        BLOCKED = "BLOQUEADO", "Bloqueado"

    request = models.ForeignKey(SolicitudColectivo, on_delete=models.CASCADE, related_name="external_accesses")
    selector = models.CharField(max_length=32, unique=True, editable=False)
    token_hash = models.CharField(max_length=64, editable=False)
    version = models.PositiveSmallIntegerField(default=1)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="accesos_colectivos_creados")
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    first_access_at = models.DateTimeField(null=True, blank=True)
    last_access_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(db_index=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="accesos_colectivos_revocados")
    used_for_submission_at = models.DateTimeField(null=True, blank=True)
    access_count = models.PositiveIntegerField(default=0)
    failed_attempts = models.PositiveSmallIntegerField(default=0)
    encrypted_recipient = models.TextField()
    recipient_hash = models.CharField(max_length=64, db_index=True)
    encrypted_contact_name = models.TextField(blank=True)
    encrypted_intro = models.TextField(blank=True)
    encrypted_instructions = models.TextField(blank=True)
    channel = models.CharField(max_length=16, default="EMAIL")
    purpose = models.CharField(max_length=32, default="CLIENT_RESPONSE")
    correlation_id = models.UUIDField(default=uuid.uuid4, editable=False)
    safe_metadata = models.JSONField(default=dict, blank=True)
    otp_hash = models.CharField(max_length=128, blank=True, editable=False)
    otp_expires_at = models.DateTimeField(null=True, blank=True)
    otp_attempts = models.PositiveSmallIntegerField(default=0)
    otp_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = (models.Index(fields=("request", "status"), name="colect_access_request"),)


class RespuestaSolicitudColectivo(models.Model):
    class Origin(models.TextChoices):
        WEB = "WEB", "Formulario web"
        EXCEL = "EXCEL", "Excel"
    class Status(models.TextChoices):
        DRAFT = "BORRADOR", "Borrador"
        SUBMITTED = "ENVIADA", "Enviada"
        SUPERSEDED = "SUPERADA", "Superada"
        APPROVED = "APROBADA", "Aprobada"

    request = models.ForeignKey(SolicitudColectivo, on_delete=models.CASCADE, related_name="responses")
    access = models.ForeignKey(AccesoExternoSolicitudColectivo, null=True, blank=True, on_delete=models.PROTECT, related_name="responses")
    version = models.PositiveIntegerField()
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT, db_index=True)
    origin = models.CharField(max_length=8, choices=Origin.choices)
    started_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    checksum = models.CharField(max_length=64)
    encrypted_client_observations = models.TextField(blank=True)
    declaration_confirmed = models.BooleanField(default=False)
    form_version = models.PositiveSmallIntegerField(default=1)
    safe_metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ("-version",)
        constraints = (models.UniqueConstraint(fields=("request", "version"), name="colect_response_version"),)


class VistaPreviaExcelSolicitudColectivo(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDIENTE", "Pendiente"
        IMPORTED = "IMPORTADA", "Importada"
        CANCELLED = "CANCELADA", "Cancelada"
        EXPIRED = "EXPIRADA", "Expirada"

    request = models.ForeignKey(SolicitudColectivo, on_delete=models.CASCADE, related_name="excel_previews")
    access = models.ForeignKey(AccesoExternoSolicitudColectivo, on_delete=models.CASCADE, related_name="excel_previews")
    response = models.OneToOneField(RespuestaSolicitudColectivo, null=True, blank=True, on_delete=models.PROTECT, related_name="excel_preview")
    selector = models.CharField(max_length=32, unique=True, editable=False)
    token_hash = models.CharField(max_length=64, editable=False)
    session_hash = models.CharField(max_length=64, editable=False)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING, db_index=True)
    stored_path = models.CharField(max_length=255)
    file_checksum = models.CharField(max_length=64)
    encrypted_payload = models.TextField(editable=False)
    summary = models.JSONField(default=dict, blank=True)
    template_version = models.PositiveSmallIntegerField()
    snapshot_revision = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)
    consumed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = (models.Index(fields=("request", "status"), name="colect_preview_request"),)


class CambioSolicitudColectivo(models.Model):
    class Action(models.TextChoices):
        UNCHANGED = "SIN_CAMBIOS", "Sin cambios"
        MODIFY = "MODIFICAR", "Modificar"
        RETIRE = "RETIRAR", "Retirar"
        INCLUDE = "INCLUIR", "Incluir"
    class Validation(models.TextChoices):
        VALID = "VALIDO", "Válido"
        WARNING = "ADVERTENCIA", "Advertencia"
        INVALID = "INVALIDO", "Inválido"

    response = models.ForeignKey(RespuestaSolicitudColectivo, on_delete=models.CASCADE, related_name="changes")
    policy = models.ForeignKey(SolicitudColectivoPoliza, null=True, blank=True, on_delete=models.PROTECT, related_name="changes")
    original_record = models.ForeignKey(SolicitudColectivoRegistro, null=True, blank=True, on_delete=models.PROTECT, related_name="response_changes")
    action = models.CharField(max_length=16, choices=Action.choices)
    functional_field = models.CharField(max_length=64)
    encrypted_previous_value = models.TextField(blank=True)
    encrypted_new_value = models.TextField(blank=True)
    validation_status = models.CharField(max_length=12, choices=Validation.choices, default=Validation.VALID)
    safe_error = models.CharField(max_length=80, blank=True)
    encrypted_observation = models.TextField(blank=True)
    position = models.PositiveIntegerField()
    encrypted_branch_payload = models.TextField(blank=True)
    checksum = models.CharField(max_length=64)

    class Meta:
        ordering = ("position", "id")


class AdjuntoSolicitudColectivo(models.Model):
    class Status(models.TextChoices):
        PENDING_SCAN = "REVISION_ANTIVIRUS", "Pendiente de revisión antivirus"
        ACCEPTED = "ACEPTADO", "Aceptado"
        REJECTED = "RECHAZADO", "Rechazado"
    request = models.ForeignKey(SolicitudColectivo, on_delete=models.CASCADE, related_name="attachments")
    response = models.ForeignKey(RespuestaSolicitudColectivo, on_delete=models.CASCADE, related_name="attachments")
    change = models.ForeignKey(CambioSolicitudColectivo, null=True, blank=True, on_delete=models.SET_NULL, related_name="attachments")
    safe_original_name = models.CharField(max_length=120)
    internal_name = models.CharField(max_length=80, unique=True)
    extension = models.CharField(max_length=8)
    detected_mime = models.CharField(max_length=80)
    size = models.PositiveIntegerField()
    checksum = models.CharField(max_length=64)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.PENDING_SCAN)
    category = models.CharField(max_length=32, default="SOPORTE")
    stored_path = models.CharField(max_length=255)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    uploaded_by_external = models.BooleanField(default=True)
    safe_metadata = models.JSONField(default=dict, blank=True)


class RevisionSolicitudColectivo(models.Model):
    class Decision(models.TextChoices):
        APPROVE = "APROBAR", "Aprobar"
        REJECT = "RECHAZAR", "Rechazar"
        CORRECTION = "REQUIERE_CORRECCION", "Requiere corrección"
        ADJUST = "APROBAR_CON_AJUSTE", "Aprobar con ajuste"
    request = models.ForeignKey(SolicitudColectivo, on_delete=models.CASCADE, related_name="reviews")
    response = models.ForeignKey(RespuestaSolicitudColectivo, on_delete=models.PROTECT, related_name="reviews")
    change = models.ForeignKey(CambioSolicitudColectivo, null=True, blank=True, on_delete=models.PROTECT, related_name="reviews")
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    decision = models.CharField(max_length=24, choices=Decision.choices)
    encrypted_approved_value = models.TextField(blank=True)
    encrypted_internal_observation = models.TextField(blank=True)
    encrypted_client_observation = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    version = models.PositiveIntegerField(default=1)
    checksum = models.CharField(max_length=64)

    class Meta:
        constraints = (models.UniqueConstraint(fields=("response", "change", "version"), name="colect_review_change_version"),)
