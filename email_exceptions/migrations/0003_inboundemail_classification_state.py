from django.db import migrations, models


def mark_existing_exceptions(apps, schema_editor):
    db_alias = schema_editor.connection.alias
    InboundEmail = apps.get_model("email_exceptions", "InboundEmail")
    EmailException = apps.get_model("email_exceptions", "EmailException")
    exception_email_ids = EmailException.objects.using(db_alias).values_list("primary_email_id", flat=True)
    InboundEmail.objects.using(db_alias).filter(pk__in=exception_email_ids).update(
        classification_status="EXCEPTION",
    )


class Migration(migrations.Migration):
    dependencies = [("email_exceptions", "0002_alter_emailauditevent_options_and_more")]

    operations = [
        migrations.AddField(
            model_name="inboundemail",
            name="classification_reason",
            field=models.CharField(blank=True, max_length=500),
        ),
        migrations.AddField(
            model_name="inboundemail",
            name="classification_rule_id",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="inboundemail",
            name="classification_status",
            field=models.CharField(
                choices=[
                    ("PENDING", "Pendiente de clasificación"),
                    ("EXCEPTION", "Excepción"),
                    ("NO_MATCH", "Sin coincidencia"),
                    ("ERROR", "Error de clasificación"),
                ],
                default="PENDING",
                max_length=12,
            ),
        ),
        migrations.AddField(
            model_name="inboundemail",
            name="processed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(mark_existing_exceptions, migrations.RunPython.noop),
    ]
