"""Capa de servicio de la app `conciliacion`.

Adapta el motor puro (`conciliador`) al mundo Django: baja los archivos subidos a
un directorio temporal, invoca la conciliación y devuelve un resultado
serializable (bytes del Excel + resumen), traduciendo errores de negocio a
`ConciliacionProcessingError`.
"""

from .processor import (
    ConciliacionProcessingError,
    ConciliacionOutput,
    procesar_conciliacion,
)

__all__ = [
    "ConciliacionProcessingError",
    "ConciliacionOutput",
    "procesar_conciliacion",
]
