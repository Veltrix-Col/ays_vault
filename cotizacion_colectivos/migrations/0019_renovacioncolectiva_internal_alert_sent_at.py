from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("cotizacion_colectivos", "0018_renovacioncolectiva_seller_label")]

    operations = [
        migrations.AddField(
            model_name="renovacioncolectiva",
            name="internal_alert_sent_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
