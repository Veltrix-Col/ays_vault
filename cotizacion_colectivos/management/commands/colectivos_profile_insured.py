from cotizacion_colectivos.relation_profiling import INSURED_SPEC

from ._relation_profile_base import RelationProfileCommand


class Command(RelationProfileCommand):
    help = "Perfila relaciones de Riesgos1 exclusivamente en Zoho Sandbox."
    spec = INSURED_SPEC
