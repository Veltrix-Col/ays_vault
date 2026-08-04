from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("cotizacion_colectivos", "0003_backfill_registro_public_key")]

    operations = [
        migrations.CreateModel(
            name="VistaPreviaExcelSolicitudColectivo",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("selector", models.CharField(editable=False, max_length=32, unique=True)),
                ("token_hash", models.CharField(editable=False, max_length=64)),
                ("session_hash", models.CharField(editable=False, max_length=64)),
                ("status", models.CharField(choices=[("PENDIENTE", "Pendiente"), ("IMPORTADA", "Importada"), ("CANCELADA", "Cancelada"), ("EXPIRADA", "Expirada")], db_index=True, default="PENDIENTE", max_length=12)),
                ("stored_path", models.CharField(max_length=255)),
                ("file_checksum", models.CharField(max_length=64)),
                ("encrypted_payload", models.TextField(editable=False)),
                ("summary", models.JSONField(blank=True, default=dict)),
                ("template_version", models.PositiveSmallIntegerField()),
                ("snapshot_revision", models.PositiveIntegerField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("expires_at", models.DateTimeField(db_index=True)),
                ("consumed_at", models.DateTimeField(blank=True, null=True)),
                ("access", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="excel_previews", to="cotizacion_colectivos.accesoexternosolicitudcolectivo")),
                ("request", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="excel_previews", to="cotizacion_colectivos.solicitudcolectivo")),
                ("response", models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="excel_preview", to="cotizacion_colectivos.respuestasolicitudcolectivo")),
            ],
        ),
        migrations.AddIndex(model_name="vistapreviaexcelsolicitudcolectivo", index=models.Index(fields=["request", "status"], name="colect_preview_request")),
    ]
