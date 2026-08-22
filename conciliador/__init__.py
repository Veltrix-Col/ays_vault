"""Motor de conciliacion de relaciones de asegurados (Movilidad, Salud, VG Voluntario, VG Deudores, ...).

Paquete puro en Python + pandas, sin dependencias de framework web: pensado
para vivir como capa de "services" dentro de un backend (Django u otro) o
para usarse desde linea de comandos via `conciliador.cli`.
"""

from conciliador.domain.models import Incidente, ModoValor, RamoConfig, ReporteConciliacion

__all__ = ["Incidente", "ModoValor", "RamoConfig", "ReporteConciliacion"]
