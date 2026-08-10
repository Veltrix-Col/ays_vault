"""Formulario de carga del Conciliador de Facturación.

Un único formulario con el selector de ramo, el número de póliza (metadata) y los
slots de archivo. La validación es defensiva pero liviana: tamaño, extensión
coherente con el ramo y una inspección anti–zip-bomb para los .xlsx (mismo
criterio que la app `soat`). El PDF se valida por firma. La lógica de negocio
(estructura interna de cada archivo) la valida el motor `conciliador`.
"""

from __future__ import annotations

import os
from zipfile import BadZipFile, ZipFile

from django import forms
from django.conf import settings

from .ramos_ui import RAMO_CHOICES, slots_de_ramo

_EXT_POR_ACCEPT = {
    ".xlsx": {".xlsx"},
    ".csv": {".csv"},
    ".xls,.xlsx": {".xls", ".xlsx"},
    ".pdf": {".pdf"},
}


def _max_bytes() -> int:
    return getattr(settings, "CONCILIACION_MAX_UPLOAD_BYTES", 25 * 1024 * 1024)


def _extension(nombre: str) -> str:
    return os.path.splitext(nombre or "")[1].lower()


def _validar_tamano(archivo) -> None:
    if archivo.size <= 0:
        raise forms.ValidationError("El archivo está vacío.")
    if archivo.size > _max_bytes():
        raise forms.ValidationError("El archivo supera el tamaño permitido.")


def _validar_xlsx_seguro(archivo) -> None:
    """Rechaza .xlsx corruptos, con rutas peligrosas o descompresión abusiva."""
    try:
        with ZipFile(archivo) as archive:
            miembros = archive.infolist()
            if not miembros or "[Content_Types].xml" not in archive.namelist():
                raise forms.ValidationError("El archivo no corresponde a un Excel válido.")
            total = 0
            for miembro in miembros:
                nombre = miembro.filename.replace("\\", "/")
                if nombre.startswith("/") or ".." in nombre.split("/"):
                    raise forms.ValidationError("El archivo contiene rutas no permitidas.")
                if nombre.lower().endswith(("vbaproject.bin", ".exe", ".dll")):
                    raise forms.ValidationError("El archivo contiene contenido no permitido.")
                total += miembro.file_size
            if total > _max_bytes() * 20:
                raise forms.ValidationError("El archivo comprimido excede el límite seguro.")
    except BadZipFile as exc:
        raise forms.ValidationError("El archivo no corresponde a un Excel válido.") from exc
    finally:
        archivo.seek(0)


def _validar_pdf(archivo) -> None:
    encabezado = archivo.read(5)
    archivo.seek(0)
    if not encabezado.startswith(b"%PDF-"):
        raise forms.ValidationError("El archivo no corresponde a un PDF válido.")


class ConciliacionUploadForm(forms.Form):
    ramo = forms.ChoiceField(choices=RAMO_CHOICES, label="Ramo")
    poliza = forms.CharField(
        label="Número de póliza",
        max_length=60,
        strip=True,
        widget=forms.TextInput(attrs={"autocomplete": "off", "inputmode": "numeric"}),
    )
    cobro = forms.FileField(label="Archivo de cobro", required=True)
    recibo = forms.FileField(label="PDF de cobro", required=True)
    relacion = forms.FileField(label="Relación de asegurados (Zoho)", required=True)
    personas = forms.FileField(label="Personas (Zoho)", required=True)
    novedades = forms.FileField(label="Novedades", required=False)

    def clean_poliza(self):
        valor = (self.cleaned_data.get("poliza") or "").strip()
        if not valor:
            raise forms.ValidationError("Indique el número de póliza.")
        return valor

    def clean(self):
        cleaned = super().clean()
        ramo = cleaned.get("ramo")
        if ramo not in dict(RAMO_CHOICES):
            return cleaned

        accept_por_campo = {slot["campo"]: slot["accept"] for slot in slots_de_ramo(ramo)}
        for campo, accept in accept_por_campo.items():
            archivo = cleaned.get(campo)
            if archivo in (None, False):
                continue
            try:
                self._validar_archivo(archivo, accept)
            except forms.ValidationError as exc:
                self.add_error(campo, exc)
        return cleaned

    @staticmethod
    def _validar_archivo(archivo, accept: str) -> None:
        _validar_tamano(archivo)
        permitidas = _EXT_POR_ACCEPT.get(accept, set())
        extension = _extension(getattr(archivo, "name", ""))
        if permitidas and extension not in permitidas:
            legibles = " o ".join(sorted(permitidas))
            raise forms.ValidationError(f"El archivo debe tener extensión {legibles}.")
        if extension == ".xlsx":
            _validar_xlsx_seguro(archivo)
        elif extension == ".pdf":
            _validar_pdf(archivo)
        archivo.seek(0)
