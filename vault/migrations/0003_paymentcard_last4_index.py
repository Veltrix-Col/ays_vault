from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("vault", "0002_security_hardening")]
    operations = [
        migrations.AlterField(
            model_name="paymentcard",
            name="last4",
            field=models.CharField(db_index=True, editable=False, max_length=4),
        ),
    ]
