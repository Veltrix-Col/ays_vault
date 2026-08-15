from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("cotizacion_colectivos", "0010_accesocotizacionindividual")]

    operations = [
        migrations.CreateModel(
            name="ColectivosTaskOutbox",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("event_kind", models.CharField(max_length=16)),
                ("event_version", models.PositiveIntegerField(default=1)),
                ("idempotency_key", models.CharField(editable=False, max_length=96, unique=True)),
                ("encrypted_payload", models.TextField(editable=False)),
                ("payload_checksum", models.CharField(editable=False, max_length=64)),
                ("status", models.CharField(choices=[("PENDIENTE", "Pendiente"), ("PUBLICADA", "Publicada"), ("BLOQUEADA", "Bloqueada"), ("CONCILIAR", "Requiere conciliación")], db_index=True, default="PENDIENTE", max_length=12)),
                ("attempts", models.PositiveSmallIntegerField(default=0)),
                ("encrypted_remote_id", models.TextField(blank=True, editable=False)),
                ("safe_error_code", models.CharField(blank=True, max_length=40)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("quotation", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="task_outbox", to="cotizacion_colectivos.cotizacionindividual")),
                ("request", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="task_outbox", to="cotizacion_colectivos.solicitudcolectivo")),
            ],
        ),
        migrations.AddConstraint(
            model_name="colectivostaskoutbox",
            constraint=models.CheckConstraint(condition=models.Q(models.Q(("quotation__isnull", True), ("request__isnull", False)), models.Q(("quotation__isnull", False), ("request__isnull", True)), _connector="OR"), name="colect_task_outbox_one_source"),
        ),
    ]
