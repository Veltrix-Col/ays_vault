import base64
import hashlib
import hmac
import json

import django.db.models.deletion
from cryptography.fernet import Fernet
from django.conf import settings
from django.db import migrations, models


def populate_security_fields(apps, schema_editor):
    PaymentCard = apps.get_model("vault", "PaymentCard")
    AuditEvent = apps.get_model("vault", "AuditEvent")
    AuditChainState = apps.get_model("vault", "AuditChainState")
    UserProfile = apps.get_model("vault", "UserProfile")

    encryption_key = settings.FIELD_ENCRYPTION_KEY.encode() if settings.FIELD_ENCRYPTION_KEY else base64.urlsafe_b64encode(hashlib.sha256(settings.SECRET_KEY.encode()).digest())
    fingerprint_key = settings.FIELD_FINGERPRINT_KEY.encode() if settings.FIELD_FINGERPRINT_KEY else hashlib.sha256(encryption_key + b":fingerprint").digest()
    cipher = Fernet(encryption_key)
    for card in PaymentCard.objects.all():
        pan = cipher.decrypt(card.encrypted_pan.encode()).decode()
        card.pan_fingerprint = hmac.new(fingerprint_key, pan.encode(), hashlib.sha256).hexdigest()
        card.save(update_fields=["pan_fingerprint"])

    previous_hash = ""
    sequence = 0
    for event in AuditEvent.objects.order_by("created_at", "pk"):
        sequence += 1
        profile = UserProfile.objects.filter(user_id=event.user_id).first() if event.user_id else None
        event.sequence = sequence
        event.actor_role = profile.role if profile else ""
        event.session_key = ""
        event.path = str((event.metadata or {}).get("path", ""))[:300]
        event.method = ""
        event.result = "SUCCESS"
        event.risk_level = "HIGH" if event.outside_office_hours else "LOW"
        event.previous_hash = previous_hash
        canonical = json.dumps({
            "sequence": event.sequence, "user_id": event.user_id, "actor_role": event.actor_role,
            "action": event.action, "card_id": event.card_id, "field_name": event.field_name,
            "reason": event.reason, "ip_address": str(event.ip_address or ""), "user_agent": event.user_agent,
            "session_key": event.session_key, "path": event.path, "method": event.method,
            "result": event.result, "risk_level": event.risk_level,
            "outside_office_hours": event.outside_office_hours, "metadata": event.metadata,
            "previous_hash": event.previous_hash, "created_at": event.created_at.isoformat(),
        }, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        event.event_hash = hashlib.sha256(canonical.encode()).hexdigest()
        event.save(update_fields=["sequence", "actor_role", "session_key", "path", "method", "result", "risk_level", "previous_hash", "event_hash"])
        previous_hash = event.event_hash
    AuditChainState.objects.update_or_create(singleton=1, defaults={"last_sequence": sequence, "last_hash": previous_hash})


class Migration(migrations.Migration):
    dependencies = [("vault", "0001_initial")]
    operations = [
        migrations.CreateModel(name="AuditChainState", fields=[("singleton", models.PositiveSmallIntegerField(default=1, editable=False, primary_key=True, serialize=False)), ("last_sequence", models.PositiveBigIntegerField(default=0)), ("last_hash", models.CharField(blank=True, max_length=64))]),
        migrations.AddField(model_name="userprofile", name="mfa_enabled", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="userprofile", name="password_changed_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AlterField(model_name="userprofile", name="active", field=models.BooleanField(default=False)),
        migrations.AlterField(model_name="userprofile", name="role", field=models.CharField(blank=True, choices=[("ADMIN", "Administrador"), ("LEADER", "Líder de cartera"), ("ANALYST", "Analista")], default="", max_length=10)),
        migrations.AddField(model_name="paymentcard", name="pan_fingerprint", field=models.CharField(editable=False, max_length=64, null=True, unique=True)),
        migrations.AddField(model_name="paymentcard", name="updated_by", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="cards_updated", to=settings.AUTH_USER_MODEL)),
        migrations.AddField(model_name="auditevent", name="sequence", field=models.PositiveBigIntegerField(null=True, unique=True)),
        migrations.AddField(model_name="auditevent", name="actor_role", field=models.CharField(blank=True, max_length=10)),
        migrations.AddField(model_name="auditevent", name="session_key", field=models.CharField(blank=True, max_length=40)),
        migrations.AddField(model_name="auditevent", name="path", field=models.CharField(blank=True, max_length=300)),
        migrations.AddField(model_name="auditevent", name="method", field=models.CharField(blank=True, max_length=10)),
        migrations.AddField(model_name="auditevent", name="result", field=models.CharField(default="SUCCESS", max_length=20)),
        migrations.AddField(model_name="auditevent", name="risk_level", field=models.CharField(choices=[("LOW", "Bajo"), ("MEDIUM", "Medio"), ("HIGH", "Alto"), ("CRITICAL", "Crítico")], default="LOW", max_length=10)),
        migrations.AddField(model_name="auditevent", name="previous_hash", field=models.CharField(blank=True, max_length=64)),
        migrations.AddField(model_name="auditevent", name="event_hash", field=models.CharField(max_length=64, null=True, unique=True)),
        migrations.AlterField(model_name="auditevent", name="action", field=models.CharField(choices=[("LOGIN", "Inicio de sesión"), ("LOGIN_FAILED", "Inicio fallido"), ("LOGOUT", "Cierre de sesión"), ("ACCESS", "Acceso"), ("VIEW", "Consulta tarjeta"), ("REVEAL", "Revelado"), ("COPY", "Copia"), ("COPY_ATTEMPT", "Intento de copia"), ("CREATE", "Creación"), ("UPDATE", "Actualización"), ("DEACTIVATE", "Desactivación"), ("DENIED", "Acceso denegado"), ("INTEGRITY_FAILURE", "Fallo de integridad")], max_length=24)),
        migrations.AlterModelOptions(name="auditevent", options={"ordering": ["-sequence"]}),
        migrations.AlterField(model_name="securityalert", name="event", field=models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, to="vault.auditevent")),
        migrations.AlterField(model_name="securityalert", name="status", field=models.CharField(choices=[("NEW", "Nueva"), ("REVIEWED", "Revisada"), ("JUSTIFIED", "Justificada"), ("ESCALATED", "Escalada"), ("CLOSED", "Cerrada")], default="NEW", max_length=20)),
        migrations.CreateModel(name="RevealGrant", fields=[("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")), ("token_hash", models.CharField(editable=False, max_length=64, unique=True)), ("field_name", models.CharField(max_length=20)), ("reason", models.CharField(max_length=240)), ("session_key", models.CharField(max_length=40)), ("expires_at", models.DateTimeField()), ("copied_at", models.DateTimeField(blank=True, null=True)), ("created_at", models.DateTimeField(auto_now_add=True)), ("card", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="vault.paymentcard")), ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL))]),
        migrations.RunPython(populate_security_fields, migrations.RunPython.noop),
        migrations.AlterField(model_name="paymentcard", name="pan_fingerprint", field=models.CharField(editable=False, max_length=64, unique=True)),
        migrations.AlterField(model_name="auditevent", name="sequence", field=models.PositiveBigIntegerField(unique=True)),
        migrations.AlterField(model_name="auditevent", name="event_hash", field=models.CharField(max_length=64, unique=True)),
    ]
