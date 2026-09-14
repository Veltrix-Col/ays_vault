"""Formulario de carga del Cotizador de Renovaciones.

A diferencia del Conciliador de Facturación (slots de archivo fijos por ramo:
cobro/recibo/novedades), aquí el número de archivos por aseguradora varía
(hoy: AXA=1, SURA=2, Bolívar=2) y crece al agregar aseguradoras -- el propio
motor `gestor_cotizaciones` ya resuelve esto por auto-detección de nombre de
archivo (`gestor_cotizaciones.core.registry.asignar_archivos`). Por eso el
formulario tiene un único campo de carga múltiple, no slots nombrados.

La validación es defensiva pero liviana (tamaño, zip-bomb en .xlsx), igual
criterio que `conciliacion.forms.ConciliacionUploadForm`. La estructura interna
de cada archivo la valida el motor.
"""
from __future__ import annotations

import os
from zipfile import BadZipFile, ZipFile

from django import forms
from django.conf import settings

from .ramos_ui import RAMO_CHOICES, aseguradoras_de_ramo, ids_conocidos_de_ramo

_EXTENSIONES_PERMITIDAS = {".xlsx", ".docx"}


def _max_bytes() -> int:
    return getattr(settings, "COTIZADOR_MAX_UPLOAD_BYTES", 25 * 1024 * 1024)


def _validar_tamano(archivo) -> None:
    if archivo.size <= 0:
        raise forms.ValidationError(f"«{archivo.name}» está vacío.")
    if archivo.size > _max_bytes():
        raise forms.ValidationError(f"«{archivo.name}» supera el tamaño permitido.")


def _validar_xlsx_seguro(archivo) -> None:
    """Rechaza .xlsx corruptos, con rutas peligrosas o descompresión abusiva."""
    try:
        with ZipFile(archivo) as paquete:
            miembros = paquete.infolist()
            if not miembros or "[Content_Types].xml" not in paquete.namelist():
                raise forms.ValidationError(f"«{archivo.name}» no corresponde a un Office válido.")
            total = 0
            for miembro in miembros:
                nombre = miembro.filename.replace("\\", "/")
                if nombre.startswith("/") or ".." in nombre.split("/"):
                    raise forms.ValidationError(f"«{archivo.name}» contiene rutas no permitidas.")
                if nombre.lower().endswith(("vbaproject.bin", ".exe", ".dll")):
                    raise forms.ValidationError(f"«{archivo.name}» contiene contenido no permitido.")
                total += miembro.file_size
            if total > _max_bytes() * 20:
                raise forms.ValidationError(f"«{archivo.name}» excede el límite seguro al descomprimir.")
    except BadZipFile as exc:
        raise forms.ValidationError(f"«{archivo.name}» no corresponde a un Office válido.") from exc
    finally:
        archivo.seek(0)


class _MultiFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class _MultiFileField(forms.FileField):
    """`allow_multiple_selected` en el widget solo hace que junte varios
    archivos (`files.getlist`); `FileField.clean()` sigue esperando uno solo,
    así que hay que iterarlo -- mismo patrón documentado en Django para
    subir múltiples archivos con un `FileField`."""

    def clean(self, data, initial=None):
        limpiar_uno = super().clean
        if isinstance(data, (list, tuple)):
            return [limpiar_uno(d, initial) for d in data]
        return limpiar_uno(data, initial)


class CotizadorUploadForm(forms.Form):
    ramo = forms.ChoiceField(choices=RAMO_CHOICES, label="Ramo")
    nit = forms.CharField(
        label="NIT del tomador",
        max_length=20,
        strip=True,
        widget=forms.TextInput(attrs={"autocomplete": "off", "inputmode": "numeric"}),
        help_text="Filtra la flota/pólizas del tomador dentro de cada cotización.",
    )
    actual = forms.ChoiceField(label="Compañía actual (vigente)", choices=())
    otra_aseguradora = forms.CharField(
        label="Nombre para archivos no reconocidos",
        max_length=40,
        required=False,
        help_text=(
            "Solo si algún archivo cargado no corresponde a ninguna aseguradora ya "
            "configurada: se procesa con el adaptador genérico (100% IA) bajo este nombre."
        ),
    )
    archivos = _MultiFileField(
        label="Cotizaciones (todas las aseguradoras, uno o varios archivos)",
        widget=_MultiFileInput(attrs={"multiple": True}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        ramo = self.data.get("ramo") if self.is_bound else None
        ramo = ramo or (self.initial or {}).get("ramo") or RAMO_CHOICES[0][0]
        self.fields["actual"].choices = aseguradoras_de_ramo(ramo)

    def clean_nit(self):
        valor = (self.cleaned_data.get("nit") or "").strip()
        if not valor:
            raise forms.ValidationError("Indique el NIT del tomador.")
        return valor

    def clean_archivos(self):
        archivos = self.cleaned_data.get("archivos") or []
        if not isinstance(archivos, list):
            archivos = [archivos]
        if not archivos:
            raise forms.ValidationError("Suba al menos un archivo de cotización.")
        for archivo in archivos:
            extension = os.path.splitext(archivo.name or "")[1].lower()
            if extension not in _EXTENSIONES_PERMITIDAS:
                raise forms.ValidationError(
                    f"«{archivo.name}»: extensión no soportada (use .xlsx o .docx)."
                )
            _validar_tamano(archivo)
            if extension == ".xlsx":
                _validar_xlsx_seguro(archivo)
        return archivos

    def clean(self):
        cleaned = super().clean()
        ramo = cleaned.get("ramo")
        otra = (cleaned.get("otra_aseguradora") or "").strip()
        if otra and ramo in dict(RAMO_CHOICES) and otra.lower() in ids_conocidos_de_ramo(ramo):
            self.add_error(
                "otra_aseguradora",
                "Ese nombre ya corresponde a una aseguradora configurada; dele otro nombre.",
            )
        cleaned["otra_aseguradora"] = otra.lower()
        return cleaned
