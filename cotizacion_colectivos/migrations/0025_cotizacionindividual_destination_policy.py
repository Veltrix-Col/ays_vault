from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("cotizacion_colectivos", "0024_renovacioncolectiva_policy_automation_enabled")]

    operations = [
        migrations.AddField(
            model_name="cotizacionindividual",
            name="destination_policy_remote_id",
            field=models.CharField(blank=True, db_index=True, max_length=30),
        ),
    ]
