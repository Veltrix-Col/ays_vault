from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("cotizacion_colectivos", "0014_renovacioncolectiva_send_stats")]

    operations = [
        migrations.AlterField(
            model_name="renovacioncolectiva",
            name="expiry_date",
            field=models.DateField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(model_name="renovacioncolectiva", name="monthly_period", field=models.CharField(blank=True, db_index=True, max_length=7)),
        migrations.AddField(model_name="renovacioncolectiva", name="policy_status", field=models.CharField(blank=True, max_length=40)),
        migrations.AddField(model_name="renovacioncolectiva", name="payment_frequency", field=models.CharField(blank=True, max_length=40)),
        migrations.AddField(model_name="renovacioncolectiva", name="link_expires_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="renovacioncolectiva", name="reminder_due_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="renovacioncolectiva", name="reminder_sent_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="renovacioncolectiva", name="encrypted_access_token", field=models.TextField(blank=True, editable=False)),
    ]
