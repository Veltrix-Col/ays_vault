"""Jerarquia de excepciones propia del dominio.

Un backend que use este paquete (p.ej. una vista de Django) puede capturar
`ConciliadorError` y traducirla a una respuesta HTTP con sentido, en vez de
dejar pasar excepciones crudas de pandas/openpyxl.
"""

from __future__ import annotations


class ConciliadorError(Exception):
    """Error base del dominio de conciliacion."""


class ArchivoNoEncontradoError(ConciliadorError):
    """No se encontro un archivo requerido (por patron de nombre o ruta explicita)."""


class ColumnaFaltanteError(ConciliadorError):
    """El archivo se pudo leer pero le falta una columna que el loader necesita."""

    def __init__(self, archivo: str, columna: str):
        self.archivo = archivo
        self.columna = columna
        super().__init__(f"'{archivo}' no tiene la columna requerida '{columna}'")


class RamoNoRegistradoError(ConciliadorError):
    """Se pidio un ramo que no existe en el registro de RamoConfig."""
