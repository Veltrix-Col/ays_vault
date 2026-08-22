from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("cotizacion_colectivos", "0006_solicitud_multipolicy")]

    operations = [
        migrations.CreateModel(
            name="WorkspacePolizaColectivo",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("workspace_key", models.CharField(editable=False, max_length=64, unique=True)),
                ("profile", models.CharField(db_index=True, max_length=12)),
                ("backend", models.CharField(max_length=12)),
                ("source_kind", models.CharField(choices=[("company", "Empresa"), ("person", "Individuo")], max_length=12)),
                ("policy_reference_hash", models.CharField(db_index=True, editable=False, max_length=64)),
                ("source_reference_hash", models.CharField(db_index=True, editable=False, max_length=64)),
                ("encrypted_snapshot", models.TextField(editable=False)),
                ("snapshot_checksum", models.CharField(editable=False, max_length=64)),
                ("snapshot_version", models.PositiveSmallIntegerField(default=1)),
                ("revision", models.PositiveIntegerField(default=1)),
                ("record_count", models.PositiveIntegerField(default=0)),
                ("warning_count", models.PositiveIntegerField(default=0)),
                ("safe_metrics", models.JSONField(blank=True, default=dict)),
                ("safe_timeline", models.JSONField(blank=True, default=list)),
                ("synced_at", models.DateTimeField(db_index=True)),
                ("expires_at", models.DateTimeField(db_index=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.AddIndex(
            model_name="workspacepolizacolectivo",
            index=models.Index(fields=["profile", "source_kind", "expires_at"], name="colect_ws_profile_exp"),
        ),
    ]
