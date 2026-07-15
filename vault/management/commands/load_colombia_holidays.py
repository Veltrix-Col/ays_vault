from datetime import date, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from vault.models import Holiday
from vault.security import audit


def easter_sunday(year):
    a = year % 19; b = year // 100; c = year % 100; d = b // 4; e = b % 4
    f = (b + 8) // 25; g = (b - f + 1) // 3; h = (19 * a + b - d - g + 15) % 30
    i = c // 4; k = c % 4; month_value = (a + 11 * h + 22 * ((2 * e + 2 * i - h - k) % 7)) // 451
    month = (h + (2 * e + 2 * i - h - k) % 7 + 114 - 7 * month_value) // 31
    day = ((h + (2 * e + 2 * i - h - k) % 7 + 114 - 7 * month_value) % 31) + 1
    return date(year, month, day)


def next_monday(value):
    return value + timedelta(days=(7 - value.weekday()) % 7)


def colombia_holidays(year):
    easter = easter_sunday(year)
    values = {
        date(year, 1, 1): "Ano Nuevo", date(year, 5, 1): "Dia del Trabajo",
        date(year, 7, 20): "Independencia de Colombia", date(year, 8, 7): "Batalla de Boyaca",
        date(year, 12, 8): "Inmaculada Concepcion", date(year, 12, 25): "Navidad",
        easter - timedelta(days=3): "Jueves Santo", easter - timedelta(days=2): "Viernes Santo",
        next_monday(easter + timedelta(days=39)): "Ascension del Senor",
        next_monday(easter + timedelta(days=60)): "Corpus Christi",
        next_monday(easter + timedelta(days=68)): "Sagrado Corazon de Jesus",
    }
    for value, name in [
        (date(year, 1, 6), "Dia de los Reyes Magos"), (date(year, 3, 19), "Dia de San Jose"),
        (date(year, 6, 29), "San Pedro y San Pablo"), (date(year, 8, 15), "Asuncion de la Virgen"),
        (date(year, 10, 12), "Dia de la Raza"), (date(year, 11, 1), "Todos los Santos"),
        (date(year, 11, 11), "Independencia de Cartagena"),
    ]:
        values[next_monday(value)] = name
    return values


class Command(BaseCommand):
    help = "Carga festivos nacionales de Colombia sin depender de servicios externos."

    def add_arguments(self, parser):
        parser.add_argument("--year", type=int, required=True)

    @transaction.atomic
    def handle(self, *args, **options):
        year = options["year"]
        if year < 2000 or year > 2100:
            raise CommandError("El ano debe estar entre 2000 y 2100.")
        created = updated = 0
        for value, name in colombia_holidays(year).items():
            holiday, was_created = Holiday.objects.update_or_create(
                date=value,
                defaults={"name": name, "national": True, "internal": False},
            )
            created += int(was_created); updated += int(not was_created)
        audit(None, "POLICY_EVALUATION", reason="Carga anual de festivos", metadata={"year": year, "created": created, "updated": updated})
        self.stdout.write(self.style.SUCCESS(f"Festivos {year}: {created} creados, {updated} actualizados."))
