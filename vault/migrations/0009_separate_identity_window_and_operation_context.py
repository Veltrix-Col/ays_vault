import django.db.models.deletion
import uuid

from django.conf import settings
from django.db import migrations, models
from django.utils import timezone


def migrate_operation_contexts(apps, schema_editor):
    Window = apps.get_model("vault", "SensitiveOperationWindow")
    Context = apps.get_model("vault", "ProtectedOperationContext")
    Grant = apps.get_model("vault", "RevealGrant")
    now = timezone.now()

    for window in Window.objects.all().iterator():
        grants = list(Grant.objects.filter(operation_window_id=window.pk).order_by("created_at", "pk"))
        grouped = {}
        for grant in grants:
            grouped.setdefault(grant.card_id, []).append(grant)
        if not grouped:
            grouped[None] = []

        for card_id, card_grants in grouped.items():
            source = card_grants[0] if card_grants else window
            context = Context.objects.create(
                identity_window_id=window.pk,
                user_id=window.user_id,
                session_hash=window.session_hash,
                card_id=card_id,
                reason=source.reason or window.reason,
                internal_reference=source.internal_reference or window.internal_reference,
                expires_at=window.expires_at,
                closed_at=now,
                close_reason="Contexto histórico migrado; requiere nueva confirmación",
            )
            Context.objects.filter(pk=context.pk).update(created_at=window.created_at)
            if card_grants:
                Grant.objects.filter(pk__in=[item.pk for item in card_grants]).update(operation_context_id=context.pk)

    # Grants creados antes de que 0008 incorporara SensitiveOperationWindow pueden
    # tener operation_window=NULL. Se preservan como historia cerrada: nunca deben
    # convertirse en una autorización reutilizable ni impedir la migración.
    orphan_grants = Grant.objects.filter(operation_window_id__isnull=True).order_by("created_at", "pk")
    orphan_groups = {}
    for grant in orphan_grants.iterator():
        orphan_groups.setdefault((grant.user_id, grant.session_key), []).append(grant)

    for (user_id, session_hash), grants in orphan_groups.items():
        first_grant = grants[0]
        last_expiry = max(item.expires_at for item in grants)
        window = Window.objects.create(
            user_id=user_id,
            session_hash=session_hash,
            purpose="protected_data",
            reason=first_grant.reason or "Contexto histórico",
            internal_reference=first_grant.internal_reference or "LEGACY",
            expires_at=last_expiry,
            revoked_at=now,
            revocation_reason="Autorización histórica migrada",
        )
        Window.objects.filter(pk=window.pk).update(created_at=first_grant.created_at)

        grants_by_card = {}
        for grant in grants:
            grants_by_card.setdefault(grant.card_id, []).append(grant)
        for card_id, card_grants in grants_by_card.items():
            source = card_grants[0]
            context = Context.objects.create(
                identity_window_id=window.pk,
                user_id=user_id,
                session_hash=session_hash,
                card_id=card_id,
                reason=source.reason or "Contexto histórico",
                internal_reference=source.internal_reference or "LEGACY",
                expires_at=max(item.expires_at for item in card_grants),
                closed_at=now,
                close_reason="Contexto histórico migrado; requiere nueva confirmación",
            )
            Context.objects.filter(pk=context.pk).update(created_at=source.created_at)
            Grant.objects.filter(pk__in=[item.pk for item in card_grants]).update(
                operation_window_id=window.pk,
                operation_context_id=context.pk,
            )


def restore_legacy_fields(apps, schema_editor):
    Window = apps.get_model("vault", "SensitiveOperationWindow")
    Context = apps.get_model("vault", "ProtectedOperationContext")
    Grant = apps.get_model("vault", "RevealGrant")

    for window in Window.objects.all().iterator():
        context = Context.objects.filter(identity_window_id=window.pk).order_by("created_at", "pk").first()
        Window.objects.filter(pk=window.pk).update(
            reason=context.reason if context else "Contexto migrado",
            internal_reference=context.internal_reference if context else "LEGACY",
        )
    for grant in Grant.objects.select_related("operation_context").all().iterator():
        context = grant.operation_context
        Grant.objects.filter(pk=grant.pk).update(
            reason=context.reason,
            internal_reference=context.internal_reference,
        )


class Migration(migrations.Migration):

    dependencies = [
        ("vault", "0008_paymentcard_encrypted_company_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ProtectedOperationContext",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("public_id", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("session_hash", models.CharField(editable=False, max_length=64)),
                ("reason", models.CharField(max_length=240)),
                ("internal_reference", models.CharField(max_length=120)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("expires_at", models.DateTimeField()),
                ("closed_at", models.DateTimeField(blank=True, null=True)),
                ("close_reason", models.CharField(blank=True, max_length=120)),
                ("card", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="operation_contexts", to="vault.paymentcard")),
                ("identity_window", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="operation_contexts", to="vault.sensitiveoperationwindow")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="vault_operation_contexts", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "indexes": [models.Index(fields=["user", "session_hash", "card", "expires_at"], name="vault_op_context_lookup")],
                "constraints": [
                    models.UniqueConstraint(condition=models.Q(("closed_at__isnull", True)), fields=("user", "session_hash"), name="vault_one_open_op_context"),
                    models.CheckConstraint(condition=models.Q(("card__isnull", False), ("closed_at__isnull", False), _connector="OR"), name="vault_active_context_has_card"),
                ],
            },
        ),
        migrations.AddField(
            model_name="revealgrant",
            name="operation_context",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="reveal_grants", to="vault.protectedoperationcontext"),
        ),
        migrations.AlterField(
            model_name="revealgrant",
            name="reason",
            field=models.CharField(max_length=240, null=True),
        ),
        migrations.AlterField(
            model_name="revealgrant",
            name="internal_reference",
            field=models.CharField(blank=True, max_length=120, null=True),
        ),
        migrations.AlterField(
            model_name="sensitiveoperationwindow",
            name="reason",
            field=models.CharField(max_length=240, null=True),
        ),
        migrations.AlterField(
            model_name="sensitiveoperationwindow",
            name="internal_reference",
            field=models.CharField(max_length=120, null=True),
        ),
        migrations.RunPython(migrate_operation_contexts, restore_legacy_fields),
        migrations.AlterField(
            model_name="revealgrant",
            name="operation_context",
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="reveal_grants", to="vault.protectedoperationcontext"),
        ),
        migrations.RemoveField(model_name="revealgrant", name="reason"),
        migrations.RemoveField(model_name="revealgrant", name="internal_reference"),
        migrations.RemoveField(model_name="sensitiveoperationwindow", name="reason"),
        migrations.RemoveField(model_name="sensitiveoperationwindow", name="internal_reference"),
        migrations.AlterField(
            model_name="auditevent",
            name="action",
            field=models.CharField(choices=[
                ("LOGIN", "Inicio de sesión"), ("LOGIN_FAILED", "Inicio fallido"), ("LOGOUT", "Cierre de sesión"),
                ("ACCESS", "Acceso"), ("VIEW", "Consulta tarjeta"), ("REVEAL", "Revelado"), ("COPY", "Copia"),
                ("COPY_ATTEMPT", "Intento de copia"), ("CREATE", "Creación"), ("UPDATE", "Actualización"),
                ("DEACTIVATE", "Desactivación"), ("DENIED", "Acceso denegado"), ("INTEGRITY_FAILURE", "Fallo de integridad"),
                ("PASSWORD_OK", "Contraseña correcta"), ("MFA_REQUIRED", "MFA requerido"), ("MFA_SUCCESS", "MFA exitoso"),
                ("MFA_FAILED", "MFA fallido"), ("MFA_ENROLL_START", "Enrolamiento MFA iniciado"),
                ("MFA_ENROLL_COMPLETE", "Enrolamiento MFA completado"), ("MFA_RECOVERY_USED", "Recuperación MFA utilizada"),
                ("MFA_RECOVERY_REGENERATED", "Códigos regenerados"), ("MFA_RESET", "MFA reiniciado"),
                ("SESSION_CREATED", "Sesión creada"), ("SESSION_REVOKED", "Sesión revocada"),
                ("SESSION_EXPIRED", "Sesión expirada"), ("SESSION_REPLACED", "Sesión reemplazada"),
                ("DEVICE_NEW", "Dispositivo nuevo"), ("DEVICE_TRUSTED", "Dispositivo reconocido"),
                ("DEVICE_BLOCKED", "Dispositivo bloqueado"), ("DEVICE_UNBLOCKED", "Dispositivo desbloqueado"),
                ("REAUTH_SUCCESS", "Reautenticación exitosa"), ("REAUTH_FAILED", "Reautenticación fallida"),
                ("PASSWORD_CHANGED", "Contraseña cambiada"), ("ALERT_CREATED", "Alerta creada"),
                ("ALERT_REVIEWED", "Alerta revisada"), ("ALERT_CLOSED", "Alerta cerrada"),
                ("ALERT_ESCALATED", "Alerta escalada"), ("ALERT_REOPENED", "Alerta reabierta"),
                ("POLICY_CHANGED", "Politica modificada"), ("EXCEPTION_CREATED", "Excepcion creada"),
                ("EXCEPTION_REVOKED", "Excepcion revocada"), ("EMAIL_SENT", "Correo enviado"),
                ("EMAIL_FAILED", "Fallo de correo"), ("POLICY_EVALUATION", "Evaluacion programada"),
                ("CRITICAL_BLOCKED", "Operación crítica bloqueada"), ("REPORT_EXPORT", "Informe exportado"),
                ("OPERATION_AUTHORIZED", "Operación protegida autorizada"),
                ("OP_CONTEXT_CONFIRMED", "Contexto de operación confirmado"),
                ("OPERATION_WINDOW_EXPIRED", "Autorización de operación expirada"),
            ], max_length=24),
        ),
    ]
