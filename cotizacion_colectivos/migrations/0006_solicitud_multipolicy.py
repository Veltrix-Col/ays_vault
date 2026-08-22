from __future__ import annotations

import hashlib

from django.db import migrations, models
import django.db.models.deletion


LEGACY_ADJUSTMENTS = ["SIN_CAMBIOS", "INCLUSION", "RETIRO", "MODIFICACION"]


def backfill_single_policy(apps, schema_editor):
    Request = apps.get_model("cotizacion_colectivos", "SolicitudColectivo")
    Policy = apps.get_model("cotizacion_colectivos", "SolicitudColectivoPoliza")
    Record = apps.get_model("cotizacion_colectivos", "SolicitudColectivoRegistro")
    Change = apps.get_model("cotizacion_colectivos", "CambioSolicitudColectivo")
    database = schema_editor.connection.alias
    for request in Request.objects.using(database).iterator(chunk_size=200):
        encrypted_snapshot = request.encrypted_snapshot or ""
        policy = Policy.objects.using(database).create(
            request_id=request.pk,
            policy_reference_hash=request.policy_reference_hash,
            encrypted_policy_token=request.encrypted_policy_token,
            masked_policy_reference=request.masked_policy_reference,
            branch_code=request.branch_code,
            branch_name=request.branch_name,
            modality="NO_DETERMINADA",
            parameter_version=1,
            enabled_adjustments=LEGACY_ADJUSTMENTS,
            encrypted_snapshot=encrypted_snapshot,
            snapshot_checksum=hashlib.sha256(encrypted_snapshot.encode()).hexdigest(),
            record_count=request.record_count,
            warnings=request.warnings,
            position=1,
            active=True,
        )
        Record.objects.using(database).filter(request_id=request.pk, policy_id__isnull=True).update(policy_id=policy.pk)
        Change.objects.using(database).filter(response__request_id=request.pk, policy_id__isnull=True).update(policy_id=policy.pk)


class Migration(migrations.Migration):
    dependencies = [("cotizacion_colectivos", "0005_alter_solicitudcolectivo_options")]

    operations = [
        migrations.CreateModel(
            name="SolicitudColectivoPoliza",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("policy_reference_hash", models.CharField(db_index=True, max_length=64)),
                ("encrypted_policy_token", models.TextField(editable=False)),
                ("masked_policy_reference", models.CharField(max_length=80)),
                ("branch_code", models.CharField(db_index=True, max_length=8)),
                ("branch_name", models.CharField(max_length=100)),
                ("modality", models.CharField(choices=[("NO_DETERMINADA", "No determinada"), ("PATRONAL", "Patronal"), ("VOLUNTARIA", "Voluntaria"), ("MIXTA", "Mixta")], default="NO_DETERMINADA", max_length=20)),
                ("insurer", models.CharField(blank=True, max_length=160)),
                ("policy_status", models.CharField(blank=True, max_length=80)),
                ("start_date", models.DateField(blank=True, null=True)),
                ("end_date", models.DateField(blank=True, null=True)),
                ("parameter_version", models.PositiveSmallIntegerField(default=1)),
                ("enabled_adjustments", models.JSONField(default=list)),
                ("encrypted_snapshot", models.TextField(editable=False)),
                ("snapshot_checksum", models.CharField(max_length=64)),
                ("record_count", models.PositiveIntegerField(default=0)),
                ("warnings", models.JSONField(blank=True, default=list)),
                ("position", models.PositiveSmallIntegerField()),
                ("active", models.BooleanField(default=True)),
                ("request", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="policies", to="cotizacion_colectivos.solicitudcolectivo")),
            ],
            options={"ordering": ("position",)},
        ),
        migrations.AddField(
            model_name="solicitudcolectivoregistro",
            name="policy",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="records", to="cotizacion_colectivos.solicitudcolectivopoliza"),
        ),
        migrations.AddField(
            model_name="cambiosolicitudcolectivo",
            name="policy",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="changes", to="cotizacion_colectivos.solicitudcolectivopoliza"),
        ),
        migrations.AddConstraint(model_name="solicitudcolectivopoliza", constraint=models.UniqueConstraint(fields=("request", "position"), name="colect_policy_position")),
        migrations.AddConstraint(model_name="solicitudcolectivopoliza", constraint=models.UniqueConstraint(fields=("request", "policy_reference_hash"), name="colect_request_policy")),
        migrations.AddIndex(model_name="solicitudcolectivopoliza", index=models.Index(fields=["request", "active"], name="colect_policy_active")),
        migrations.AddIndex(model_name="solicitudcolectivopoliza", index=models.Index(fields=["branch_code", "active"], name="colect_policy_branch")),
        migrations.RunPython(backfill_single_policy, migrations.RunPython.noop),
    ]
