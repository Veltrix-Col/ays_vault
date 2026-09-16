from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("email_exceptions", "0003_inboundemail_classification_state"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ExceptionCase",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("case_key", models.CharField(max_length=255, unique=True)),
                ("organization", models.CharField(blank=True, max_length=120)),
                ("family", models.CharField(blank=True, max_length=120)),
                ("event_type", models.CharField(blank=True, max_length=120)),
                ("status", models.CharField(choices=[("OPEN", "Abierto"), ("PENDING", "Pendiente"), ("RESOLVED", "Resuelto")], default="OPEN", max_length=12)),
                ("action_type", models.CharField(blank=True, max_length=40)),
                ("scope", models.CharField(choices=[("CASE", "Caso"), ("BATCH", "Lote"), ("PLATFORM", "Plataforma"), ("UNKNOWN", "Desconocido")], default="CASE", max_length=10)),
                ("opened_at", models.DateTimeField()),
                ("last_activity_at", models.DateTimeField()),
                ("resolved_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["-last_activity_at", "-pk"],
                "indexes": [
                    models.Index(fields=["status", "last_activity_at"], name="email_excep_status_622122_idx"),
                    models.Index(fields=["organization", "event_type"], name="email_excep_organiz_c34fee_idx"),
                    models.Index(fields=["scope", "status"], name="email_excep_scope_a84398_idx"),
                ],
            },
        ),
        migrations.CreateModel(
            name="CaseMessage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("role", models.CharField(choices=[("OPENING", "Apertura"), ("FOLLOW_UP", "Seguimiento"), ("RESOLUTION", "Resolución"), ("CONTEXT", "Contexto")], max_length=10)),
                ("correlation_method", models.CharField(choices=[("FUNCTIONAL_ID", "Identificador funcional"), ("CONVERSATION", "Conversación"), ("STRUCTURED", "Estructurada"), ("MANUAL", "Manual"), ("BACKFILL", "Backfill"), ("NEW_CASE", "Caso nuevo")], max_length=20)),
                ("correlation_confidence", models.CharField(choices=[("HIGH", "Alta"), ("MEDIUM", "Media"), ("LOW", "Baja")], max_length=6)),
                ("correlation_reason", models.CharField(blank=True, max_length=500)),
                ("linked_at", models.DateTimeField(auto_now_add=True)),
                ("case", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="messages", to="email_exceptions.exceptioncase")),
                ("email", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="case_links", to="email_exceptions.inboundemail")),
                ("linked_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="email_case_messages_linked", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["linked_at", "pk"],
                "indexes": [
                    models.Index(fields=["case", "linked_at"], name="email_excep_case_id_080762_idx"),
                    models.Index(fields=["email"], name="email_excep_email_i_6934ff_idx"),
                ],
            },
        ),
        migrations.AddField(
            model_name="emailexception",
            name="case",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="exceptions", to="email_exceptions.exceptioncase"),
        ),
        migrations.AddConstraint(
            model_name="casemessage",
            constraint=models.UniqueConstraint(fields=("case", "email"), name="email_unique_case_message"),
        ),
    ]
