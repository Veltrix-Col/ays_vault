import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("vault", "0009_separate_identity_window_and_operation_context"),
    ]

    operations = [
        migrations.CreateModel(
            name="PendingSensitiveOperation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("public_id", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("session_hash", models.CharField(editable=False, max_length=64)),
                ("purpose", models.CharField(max_length=40)),
                ("action", models.CharField(max_length=40)),
                ("target_type", models.CharField(max_length=40)),
                ("target_id", models.PositiveBigIntegerField()),
                ("reason", models.CharField(max_length=240)),
                ("safe_payload", models.JSONField(blank=True, default=dict)),
                ("success_url", models.CharField(max_length=240)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("PENDING", "Pendiente"),
                            ("PROCESSING", "En proceso"),
                            ("COMPLETED", "Completada"),
                        ],
                        default="PENDING",
                        max_length=12,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("expires_at", models.DateTimeField()),
                ("consumed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="vault_pending_operations",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["user", "session_hash", "purpose", "expires_at"],
                        name="vault_pending_op_lookup",
                    ),
                ],
            },
        ),
    ]
