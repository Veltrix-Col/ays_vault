from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("cotizacion_colectivos", "0016_colectivosoperationalsetting")]

    operations = [
        migrations.AddField(
            model_name="renovacioncolectiva",
            name="automation_eligible",
            field=models.BooleanField(default=False, db_index=True),
        ),
    ]
