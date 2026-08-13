from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("cotizacion_colectivos", "0008_cotizacionindividual_adjunto"),
    ]

    operations = [
        migrations.CreateModel(
            name="NotificacionCotizacionIndividual",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(default="Cotización individual recibida", max_length=120)),
                ("message", models.CharField(max_length=240)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("read_at", models.DateTimeField(blank=True, null=True)),
                ("deduplication_key", models.CharField(max_length=120)),
                ("quotation", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="notifications", to="cotizacion_colectivos.cotizacionindividual")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="notificaciones_cotizacion_individual", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ("-created_at", "-pk")},
        ),
        migrations.AddConstraint(
            model_name="notificacioncotizacionindividual",
            constraint=models.UniqueConstraint(fields=("user", "deduplication_key"), name="colect_individual_notification_dedup"),
        ),
    ]
