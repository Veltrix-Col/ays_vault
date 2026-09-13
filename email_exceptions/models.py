from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

class InboundEmail(models.Model):
    source_mailbox = models.EmailField()
    external_message_id = models.CharField(max_length=500)
    conversation_id = models.CharField(max_length=500, blank=True)
    received_at = models.DateTimeField()
    from_name = models.CharField(max_length=255, blank=True)
    from_email = models.EmailField(blank=True)
    from_domain = models.CharField(max_length=255, blank=True)
    to = models.JSONField(default=list, blank=True)
    cc = models.JSONField(default=list, blank=True)
    subject = models.CharField(max_length=998, blank=True)
    body_text = models.TextField(blank=True)
    has_attachments = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ["-received_at", "-pk"]
        constraints = [models.UniqueConstraint(fields=("source_mailbox", "external_message_id"), name="email_unique_source_message")]
        indexes = [models.Index(fields=("source_mailbox", "received_at")), models.Index(fields=("conversation_id",))]

class EmailAttachment(models.Model):
    email = models.ForeignKey(InboundEmail, related_name="attachments", on_delete=models.CASCADE)
    filename = models.CharField(max_length=255)
    content_type = models.CharField(max_length=160, blank=True)
    size = models.PositiveBigIntegerField(default=0)
    file = models.FileField(upload_to="email_exceptions/%Y/%m/", blank=True)
    checksum = models.CharField(max_length=64, blank=True)
    is_inline = models.BooleanField(default=False)
    is_documental = models.BooleanField(default=False)

class EmailPolicyProfile(models.Model):
    code = models.CharField(max_length=80)
    source_mailbox = models.EmailField(unique=True)
    version = models.CharField(max_length=32)
    status = models.CharField(max_length=20, choices=(("ACTIVE", "Activa"), ("REVIEW_ONLY", "Solo revisión"), ("INACTIVE", "Inactiva")), default="INACTIVE")
    active = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=("code", "version"), name="email_unique_policy_version")]

class EmailException(models.Model):
    PENDING = "PENDING"; MANAGED = "MANAGED"; IGNORED = "IGNORED"; ERROR = "ERROR"
    STATUS_CHOICES = ((PENDING, "Pendiente"), (MANAGED, "Gestionada"), (IGNORED, "Ignorada"), (ERROR, "Error"))
    primary_email = models.ForeignKey(InboundEmail, related_name="primary_exceptions", on_delete=models.PROTECT)
    last_subject = models.CharField(max_length=998, blank=True)
    organization = models.CharField(max_length=120, blank=True)
    family = models.CharField(max_length=120, blank=True)
    event_type = models.CharField(max_length=120, blank=True)
    exception_reason = models.CharField(max_length=500, blank=True)
    confidence = models.DecimalField(max_digits=5, decimal_places=2, default=0, validators=[MinValueValidator(0), MaxValueValidator(100)])
    policy_profile = models.ForeignKey(EmailPolicyProfile, null=True, blank=True, on_delete=models.SET_NULL)
    rule_id = models.CharField(max_length=120, blank=True)
    correlation_key = models.CharField(max_length=255, blank=True)
    classification_details = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=PENDING)
    detected_at = models.DateTimeField(auto_now_add=True)
    handled_at = models.DateTimeField(null=True, blank=True)
    handled_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="email_exceptions_handled")
    ignored_reason = models.CharField(max_length=80, blank=True)
    ignored_comment = models.TextField(blank=True)
    error_code = models.CharField(max_length=80, blank=True)
    class Meta:
        ordering = ["-detected_at", "-pk"]
        indexes = [models.Index(fields=("status", "detected_at")), models.Index(fields=("organization", "event_type")), models.Index(fields=("correlation_key",))]

class EmailExceptionMessage(models.Model):
    exception = models.ForeignKey(EmailException, related_name="messages", on_delete=models.CASCADE)
    email = models.ForeignKey(InboundEmail, related_name="exception_links", on_delete=models.CASCADE)
    linked_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=("exception", "email"), name="email_unique_exception_message")]

class EmailAuditEvent(models.Model):
    exception = models.ForeignKey(EmailException, related_name="audit_events", on_delete=models.CASCADE)
    event_type = models.CharField(max_length=40)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    timestamp = models.DateTimeField(auto_now_add=True)
    metadata = models.JSONField(default=dict, blank=True)
    class Meta:
        ordering = ["timestamp", "pk"]

class ZohoTaskCreation(models.Model):
    exception = models.OneToOneField(EmailException, related_name="zoho_task", on_delete=models.CASCADE)
    task_id = models.CharField(max_length=80, blank=True)
    task_url = models.URLField(blank=True)
    zoho_owner_id = models.CharField(max_length=120, blank=True)
    zoho_owner_name = models.CharField(max_length=255, blank=True)
    subject = models.CharField(max_length=255)
    due_date = models.DateField(null=True, blank=True)
    description = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    technical_status = models.CharField(max_length=30, default="PENDING")
