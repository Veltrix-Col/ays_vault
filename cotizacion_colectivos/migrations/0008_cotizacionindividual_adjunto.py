import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("cotizacion_colectivos", "0007_workspacepolizacolectivo"),
    ]

    operations = [
        migrations.CreateModel(
            name="CotizacionIndividual",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("public_id", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("branch_code", models.CharField(db_index=True, max_length=16)),
                ("branch_slug", models.CharField(max_length=24)),
                ("schema_version", models.PositiveSmallIntegerField(default=1)),
                ("status", models.CharField(choices=[("RECIBIDA", "Recibida")], default="RECIBIDA", max_length=12)),
                ("encrypted_payload", models.TextField(editable=False)),
                ("payload_checksum", models.CharField(editable=False, max_length=64)),
                ("context_hash", models.CharField(blank=True, db_index=True, editable=False, max_length=64)),
                ("item_count", models.PositiveSmallIntegerField(default=0)),
                ("attachment_count", models.PositiveSmallIntegerField(default=0)),
                ("submitted_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="cotizaciones_individuales_creadas", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ("-submitted_at", "-pk"),
                "permissions": (("create_individual_quotation", "Puede crear cotizaciones individuales"), ("view_individual_quotation", "Puede ver cotizaciones individuales")),
            },
        ),
        migrations.CreateModel(
            name="AdjuntoCotizacionIndividual",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("safe_original_name", models.CharField(max_length=80)),
                ("internal_name", models.CharField(editable=False, max_length=96, unique=True)),
                ("extension", models.CharField(max_length=8)),
                ("detected_mime", models.CharField(max_length=80)),
                ("size", models.PositiveIntegerField()),
                ("checksum", models.CharField(editable=False, max_length=64)),
                ("stored_path", models.CharField(editable=False, max_length=255)),
                ("category", models.CharField(default="SOPORTE", max_length=32)),
                ("uploaded_at", models.DateTimeField(auto_now_add=True)),
                ("safe_metadata", models.JSONField(blank=True, default=dict)),
                ("quotation", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="attachments", to="cotizacion_colectivos.cotizacionindividual")),
            ],
        ),
    ]
