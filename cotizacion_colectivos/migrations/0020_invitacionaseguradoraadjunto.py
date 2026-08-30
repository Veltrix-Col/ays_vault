from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("cotizacion_colectivos", "0019_renovacioncolectiva_internal_alert_sent_at"),
    ]

    operations = [
        migrations.CreateModel(
            name="InvitacionAseguradoraAdjunto",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("policy_record_id", models.CharField(db_index=True, max_length=24)),
                ("insurer_code", models.CharField(max_length=32)),
                ("template_code", models.CharField(blank=True, max_length=64)),
                ("safe_original_name", models.CharField(max_length=120)),
                ("internal_name", models.CharField(editable=False, max_length=96, unique=True)),
                ("extension", models.CharField(max_length=8)),
                ("detected_mime", models.CharField(max_length=80)),
                ("size", models.PositiveIntegerField()),
                ("checksum", models.CharField(db_index=True, max_length=64)),
                ("stored_path", models.CharField(editable=False, max_length=255)),
                ("safe_metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("policy_record_id", "insurer_code", "template_code", "checksum"),
                        name="colect_invite_att_checksum",
                    ),
                ],
            },
        ),
    ]
