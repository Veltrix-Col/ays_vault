from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("cotizacion_colectivos", "0020_invitacionaseguradoraadjunto")]

    operations = [
        migrations.CreateModel(
            name="NovedadIngresoZoho",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("item_key", models.CharField(max_length=120)),
                ("branch_code", models.CharField(blank=True, max_length=24)),
                ("policy_remote_id", models.CharField(blank=True, max_length=64)),
                ("encrypted_payload", models.TextField(editable=False)),
                ("payload_hash", models.CharField(max_length=64)),
                ("status", models.CharField(choices=[("PENDING", "Pendiente"), ("PROCESSING", "Procesando"), ("PUBLISHED", "Publicado"), ("BLOCKED", "Bloqueado"), ("RECONCILE_REQUIRED", "Requiere conciliación")], db_index=True, default="PENDING", max_length=24)),
                ("contact_zoho_id", models.CharField(blank=True, max_length=64)),
                ("risk_zoho_id", models.CharField(blank=True, max_length=64)),
                ("subrisk_zoho_id", models.CharField(blank=True, max_length=64)),
                ("safe_error", models.CharField(blank=True, max_length=240)),
                ("reconcile_required", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("request", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="ingress_items", to="cotizacion_colectivos.solicitudcolectivo")),
            ],
            options={"constraints": [models.UniqueConstraint(fields=("request", "item_key"), name="colect_ingress_item_unique")], "indexes": [models.Index(fields=("request", "status"), name="colect_ingress_status")]},
        ),
    ]
