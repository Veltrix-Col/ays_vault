from django.db import migrations


def clear_recovered_legacy_company(apps, schema_editor):
    PaymentCard = apps.get_model("vault", "PaymentCard")
    PaymentCard.objects.exclude(company_name="").exclude(encrypted_company="").update(
        encrypted_company=""
    )


def restore_recovered_legacy_company(apps, schema_editor):
    from vault.crypto import encrypt

    PaymentCard = apps.get_model("vault", "PaymentCard")
    for card in PaymentCard.objects.filter(encrypted_company="").exclude(
        company_name=""
    ).iterator():
        card.encrypted_company = encrypt(card.company_name.strip())
        card.save(update_fields=["encrypted_company"])


class Migration(migrations.Migration):

    dependencies = [
        ("vault", "0012_paymentcard_company_name_and_encrypted_code"),
    ]

    operations = [
        migrations.RunPython(
            clear_recovered_legacy_company,
            restore_recovered_legacy_company,
        ),
    ]
