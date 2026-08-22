from cotizacion_colectivos.relation_profiling import RISK_SPEC

from ._relation_profile_base import RelationProfileCommand


class Command(RelationProfileCommand):
    help = "Perfila relaciones de Riesgos exclusivamente en Zoho Sandbox."
    spec = RISK_SPEC
