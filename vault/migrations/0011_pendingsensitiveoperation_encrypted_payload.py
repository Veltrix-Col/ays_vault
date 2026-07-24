from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("vault", "0010_pendingsensitiveoperation"),
    ]

    operations = [
        migrations.AddField(
            model_name="pendingsensitiveoperation",
            name="encrypted_payload",
            field=models.TextField(blank=True, editable=False),
        ),
    ]
