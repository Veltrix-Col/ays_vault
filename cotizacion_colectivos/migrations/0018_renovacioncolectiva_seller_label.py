from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("cotizacion_colectivos", "0017_renovacioncolectiva_automation_eligible")]

    operations = [
        migrations.AddField(
            model_name="renovacioncolectiva",
            name="seller_label",
            field=models.CharField(blank=True, default="", max_length=120),
        ),
    ]
