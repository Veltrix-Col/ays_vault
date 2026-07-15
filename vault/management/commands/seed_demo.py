from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from vault.models import PaymentCard, UserProfile


def with_luhn_check_digit(prefix):
    for digit in range(10):
        candidate = f"{prefix}{digit}"
        total = 0
        parity = len(candidate) % 2
        for index, character in enumerate(candidate):
            value = int(character)
            if index % 2 == parity:
                value = value * 2 - 9 if value * 2 > 9 else value * 2
            total += value
        if total % 10 == 0:
            return candidate
    raise RuntimeError("No fue posible generar el dato demo.")


class Command(BaseCommand):
    help = "Crea usuarios individuales y 30 tarjetas ficticias idempotentes."

    def add_arguments(self, parser):
        parser.add_argument("--reset-demo", action="store_true", help="Elimina únicamente las tarjetas Cliente Demo antes de recrearlas.")

    @transaction.atomic
    def handle(self, *args, **options):
        User = get_user_model()
        users = {}
        # Preserve but disable legacy shared-style demo accounts from earlier builds.
        for legacy in User.objects.filter(username__in=["adminvault", "lidercartera", "analistacartera"]):
            legacy.is_active = False
            legacy.save(update_fields=["is_active"])
            if hasattr(legacy, "vault_profile"):
                legacy.vault_profile.active = False
                legacy.vault_profile.save(update_fields=["active"])
        people = [
            ("admin.seguridad", UserProfile.ADMIN, "Adriana", "Seguridad"),
            ("laura.cartera", UserProfile.LEADER, "Laura", "Cartera"),
            ("andres.analista", UserProfile.ANALYST, "Andrés", "Analista"),
        ]
        for username, role, first_name, last_name in people:
            user, _ = User.objects.get_or_create(username=username, defaults={"first_name": first_name, "last_name": last_name, "email": f"{username}@example.invalid"})
            user.set_password("DemoSeguro2026!")
            user.is_active = True
            user.is_staff = role == UserProfile.ADMIN
            user.is_superuser = role == UserProfile.ADMIN
            user.save()
            user.vault_profile.role = role
            user.vault_profile.active = True
            user.vault_profile.save()
            users[role] = user

        if options["reset_demo"]:
            PaymentCard.objects.filter(client_name__startswith="Cliente Demo ").delete()

        brands = ["VISA", "MC", "AMEX"]
        for index in range(1, 31):
            brand = brands[(index - 1) % len(brands)]
            if brand == "VISA":
                pan = with_luhn_check_digit(f"4111111111{index:05d}")
            elif brand == "MC":
                pan = with_luhn_check_digit(f"5555555555{index:05d}")
            else:
                pan = with_luhn_check_digit(f"3782822463{index:04d}")
            client_name = f"Cliente Demo {index:02d}"
            card = PaymentCard.objects.filter(client_name=client_name).first()
            if not card:
                card = PaymentCard(client_name=client_name, created_by=users[UserProfile.LEADER])
            card.cardholder_name = f"Titular Ficticio {index:02d}"
            card.brand = brand
            card.purpose = "Pago de obligaciones autorizadas — dato completamente ficticio"
            card.active = index % 10 != 0
            card.updated_by = users[UserProfile.LEADER]
            card.set_pan(pan)
            card.set_expiry(f"{(index % 12) + 1:02d}/{27 + (index % 4):02d}")
            card.save()

        self.stdout.write(self.style.SUCCESS("Demo lista: 30 tarjetas ficticias y tres usuarios individuales. Consulte README para credenciales."))
