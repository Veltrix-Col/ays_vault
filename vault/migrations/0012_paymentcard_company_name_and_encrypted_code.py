from django.db import migrations, models


def move_legacy_company_to_administrative_field(apps, schema_editor):
    from vault.crypto import decrypt

    PaymentCard = apps.get_model("vault", "PaymentCard")
    for card in PaymentCard.objects.exclude(encrypted_company="").iterator():
        try:
            company_name = decrypt(card.encrypted_company).strip()
        except ValueError:
            # El cifrado legado se conserva intacto. Nunca se interpreta como
            # Código ni se elimina cuando la llave histórica no está disponible.
            continue
        card.company_name = company_name
        card.save(update_fields=["company_name"])


def restore_legacy_encrypted_company(apps, schema_editor):
    from vault.crypto import encrypt

    PaymentCard = apps.get_model("vault", "PaymentCard")
    for card in PaymentCard.objects.filter(encrypted_company="").exclude(company_name="").iterator():
        card.encrypted_company = encrypt(card.company_name.strip())
        card.save(update_fields=["encrypted_company"])


class Migration(migrations.Migration):

    dependencies = [
        ("vault", "0011_pendingsensitiveoperation_encrypted_payload"),
    ]

    operations = [
        migrations.AddField(
            model_name="paymentcard",
            name="company_name",
            field=models.CharField(blank=True, max_length=160),
        ),
        migrations.AddField(
            model_name="paymentcard",
            name="encrypted_code",
            field=models.TextField(blank=True, editable=False),
        ),
        migrations.RunPython(
            move_legacy_company_to_administrative_field,
            restore_legacy_encrypted_company,
        ),
    ]
