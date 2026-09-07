from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("cotizacion_colectivos", "0021_novedadingresozho")]

    operations = [
        migrations.AddField(
            model_name="novedadingresozoho",
            name="review_status",
            field=models.CharField(blank=True, max_length=24),
        ),
        migrations.AddField(
            model_name="novedadingresozoho",
            name="review_contact_id",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name="novedadingresozoho",
            name="review_error",
            field=models.CharField(blank=True, max_length=240),
        ),
        migrations.AddField(
            model_name="novedadingresozoho",
            name="reviewed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
