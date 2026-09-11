from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("cotizacion_colectivos", "0023_billingexceptionrefreshrun_billingoperationalcase_and_more")]

    operations = [
        migrations.CreateModel(
            name="ColectivosPolicyAutomationPreference",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("policy_remote_id", models.CharField(editable=False, max_length=30, unique=True)),
                ("enabled", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
        ),
    ]
