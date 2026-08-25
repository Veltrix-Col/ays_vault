from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("cotizacion_colectivos", "0012_cotizacionindividual_safe_metadata")]

    operations = [
        migrations.CreateModel(
            name="RenovacionColectiva",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("cycle_key", models.CharField(editable=False, max_length=120, unique=True)),
                ("policy_remote_id", models.CharField(db_index=True, editable=False, max_length=30)),
                ("policy_token", models.TextField(editable=False)),
                ("masked_policy", models.CharField(max_length=120)),
                ("client_label", models.CharField(max_length=180)),
                ("branch_name", models.CharField(max_length=120)),
                ("line_of_business", models.CharField(default="Colectivo", max_length=80)),
                ("expiry_date", models.DateField(db_index=True)),
                ("encrypted_recipient", models.TextField(blank=True, editable=False)),
                ("recipient_hash", models.CharField(blank=True, db_index=True, editable=False, max_length=64)),
                ("selected", models.BooleanField(db_index=True, default=False)),
                ("scheduled_for", models.DateField(db_index=True)),
                ("status", models.CharField(choices=[("PROGRAMMED", "Programado"), ("PROCESSING", "Procesando"), ("SENT", "Enviado"), ("RESPONDED", "Respondido"), ("ALERT", "En alerta"), ("ERROR", "Error"), ("CANCELLED", "Cancelado")], db_index=True, default="PROGRAMMED", max_length=16)),
                ("sent_at", models.DateTimeField(blank=True, null=True)),
                ("responded_at", models.DateTimeField(blank=True, null=True)),
                ("last_activity_at", models.DateTimeField(blank=True, null=True)),
                ("error_code", models.CharField(blank=True, max_length=40)),
                ("safe_error", models.CharField(blank=True, max_length=240)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("access", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="renewal_cycles", to="cotizacion_colectivos.accesoexternosolicitudcolectivo")),
                ("request", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="renewal_cycles", to="cotizacion_colectivos.solicitudcolectivo")),
            ],
            options={"indexes": [models.Index(fields=["selected", "scheduled_for", "status"], name="colect_ren_due")]},
        ),
    ]
