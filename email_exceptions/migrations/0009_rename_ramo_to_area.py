from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("email_exceptions", "0008_case_level_task_intent"),
    ]

    operations = [
        migrations.RenameField(
            model_name="zohotaskcreation",
            old_name="ramo",
            new_name="area",
        ),
    ]
