from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("vault", "0013_clear_recovered_legacy_company")]

    operations = [
        migrations.AddField(
            model_name="paymentcard",
            name="identity_document",
            field=models.CharField(blank=True, default="", max_length=40),
        ),
        migrations.AddField(
            model_name="paymentcard",
            name="email",
            field=models.EmailField(blank=True, default="", max_length=254),
        ),
        migrations.AddField(
            model_name="paymentcard",
            name="phone",
            field=models.CharField(blank=True, default="", max_length=40),
        ),
    ]
