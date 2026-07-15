from django.core.management.base import BaseCommand, CommandError

from vault.security import verify_audit_chain


class Command(BaseCommand):
    help = "Verifica secuencia, enlaces y hashes de la cadena de auditoría."

    def handle(self, *args, **options):
        valid, position = verify_audit_chain()
        if not valid:
            raise CommandError(f"Cadena de auditoría inválida en la secuencia {position}.")
        self.stdout.write(self.style.SUCCESS(f"Cadena de auditoría íntegra: {position} evento(s)."))
