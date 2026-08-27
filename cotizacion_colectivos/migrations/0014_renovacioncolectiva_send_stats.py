from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("cotizacion_colectivos", "0013_renovacioncolectiva")]

    operations = [
        migrations.AddField(
            model_name="renovacioncolectiva",
            name="send_attempts",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="renovacioncolectiva",
            name="last_sent_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
