from cotizacion_colectivos.relation_profiling import POLICY_SPEC

from ._relation_profile_base import RelationProfileCommand


class Command(RelationProfileCommand):
    help = "Perfila relaciones de Polizas exclusivamente en Zoho Sandbox."
    spec = POLICY_SPEC
