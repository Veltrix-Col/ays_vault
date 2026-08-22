import uuid

from django.db import migrations, models


def backfill_public_keys(apps, schema_editor):
    Registro = apps.get_model("cotizacion_colectivos", "SolicitudColectivoRegistro")
    for record in Registro.objects.filter(public_key__isnull=True).iterator(chunk_size=500):
        record.public_key = uuid.uuid4()
        record.save(update_fields=("public_key",))


class Migration(migrations.Migration):
    dependencies = [
        ("cotizacion_colectivos", "0002_alter_solicitudcolectivo_options_and_more"),
    ]

    operations = [
        migrations.RunPython(backfill_public_keys, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="solicitudcolectivoregistro",
            name="public_key",
            field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
        ),
    ]
