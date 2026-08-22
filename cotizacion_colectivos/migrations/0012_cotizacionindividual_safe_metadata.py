from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("cotizacion_colectivos", "0011_colectivostaskoutbox")]

    operations = [
        migrations.AddField(
            model_name="cotizacionindividual",
            name="safe_metadata",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
