from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("cotizacion_colectivos", "0009_notificacioncotizacionindividual"),
    ]

    operations = [
        migrations.CreateModel(
            name="AccesoCotizacionIndividual",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("selector", models.CharField(editable=False, max_length=32, unique=True)),
                ("token_hash", models.CharField(editable=False, max_length=64)),
                ("encrypted_context", models.TextField(editable=False)),
                ("context_checksum", models.CharField(db_index=True, editable=False, max_length=64)),
                ("encrypted_recipient", models.TextField(editable=False)),
                ("recipient_hash", models.CharField(db_index=True, editable=False, max_length=64)),
                ("status", models.CharField(choices=[("ACTIVO", "Activo"), ("VERIFICADO", "Verificado"), ("USADO", "Usado"), ("EXPIRADO", "Expirado"), ("REVOCADO", "Revocado"), ("BLOQUEADO", "Bloqueado")], db_index=True, default="ACTIVO", max_length=12)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("first_access_at", models.DateTimeField(blank=True, null=True)),
                ("last_access_at", models.DateTimeField(blank=True, null=True)),
                ("expires_at", models.DateTimeField(db_index=True)),
                ("used_at", models.DateTimeField(blank=True, null=True)),
                ("access_count", models.PositiveIntegerField(default=0)),
                ("failed_attempts", models.PositiveSmallIntegerField(default=0)),
                ("otp_hash", models.CharField(blank=True, editable=False, max_length=128)),
                ("otp_expires_at", models.DateTimeField(blank=True, null=True)),
                ("otp_attempts", models.PositiveSmallIntegerField(default=0)),
                ("otp_used_at", models.DateTimeField(blank=True, null=True)),
                ("safe_metadata", models.JSONField(blank=True, default=dict)),
                ("created_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="accesos_cotizacion_individual_creados", to=settings.AUTH_USER_MODEL)),
                ("quotation", models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="external_access", to="cotizacion_colectivos.cotizacionindividual")),
            ],
        ),
        migrations.AddIndex(
            model_name="accesocotizacionindividual",
            index=models.Index(fields=["created_by", "status"], name="colect_ind_access_actor"),
        ),
    ]
