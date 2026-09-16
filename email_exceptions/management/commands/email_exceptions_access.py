from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from email_exceptions.permissions import OPERATE_PERMISSION, VIEW_PERMISSION
from intranet_sso.models import IntranetPrincipal


GROUP_PERMISSIONS = {
    "viewer": ("Email Exceptions Viewer", (VIEW_PERMISSION,)),
    "operator": (
        "Email Exceptions Operator",
        (VIEW_PERMISSION, OPERATE_PERMISSION),
    ),
}


class Command(BaseCommand):
    help = "Asigna o revoca acceso VIEWER/OPERATOR a una identidad SSO validada."

    def add_arguments(self, parser):
        parser.add_argument("--subject", required=True, help="Subject SSO exacto (normalizado).")
        parser.add_argument("--role", required=True, choices=("viewer", "operator", "none"))

    @transaction.atomic
    def handle(self, *args, **options):
        subject = options["subject"].strip().casefold()
        if not subject:
            raise CommandError("El subject no puede estar vacío.")
        principal = (
            IntranetPrincipal.objects.select_related("user")
            .filter(subject=subject, user__is_active=True)
            .first()
        )
        if principal is None:
            raise CommandError("No existe una identidad SSO activa con ese subject.")

        for group_name, _ in GROUP_PERMISSIONS.values():
            group = Group.objects.filter(name=group_name).first()
            if group:
                principal.user.groups.remove(group)

        role = options["role"]
        if role != "none":
            group_name, codenames = GROUP_PERMISSIONS[role]
            group, _ = Group.objects.get_or_create(name=group_name)
            permissions = Permission.objects.filter(
                content_type__app_label="email_exceptions",
                codename__in=[codename.rsplit(".", 1)[1] for codename in codenames],
            )
            if permissions.count() != len(codenames):
                raise CommandError("No se encontraron todos los permisos del módulo; ejecute migrate.")
            group.permissions.set(permissions)
            principal.user.groups.add(group)

        self.stdout.write(self.style.SUCCESS(f"Acceso {role} aplicado a la identidad SSO."))
