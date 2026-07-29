from zipfile import BadZipFile, ZipFile

from django import forms
from django.conf import settings

from .services import legacy_processor as legacy


class SoatUploadForm(forms.Form):
    source_file = forms.FileField(
        label="Reporte de Zoho",
        help_text="Adjunte el reporte Excel exportado desde Zoho.",
        widget=forms.ClearableFileInput(attrs={
            "accept": ".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        }),
    )

    def clean_source_file(self):
        uploaded = self.cleaned_data["source_file"]
        if uploaded.size <= 0:
            raise forms.ValidationError("El archivo está vacío.")
        if uploaded.size > settings.SOAT_MAX_UPLOAD_BYTES:
            raise forms.ValidationError("El archivo supera el tamaño permitido.")
        try:
            with ZipFile(uploaded) as archive:
                members = archive.infolist()
                if not members or "[Content_Types].xml" not in archive.namelist():
                    raise forms.ValidationError("El archivo no corresponde a un Excel válido.")
                total = 0
                for member in members:
                    name = member.filename.replace("\\", "/")
                    if name.startswith("/") or ".." in name.split("/"):
                        raise forms.ValidationError("El archivo contiene rutas no permitidas.")
                    if name.lower().endswith(("vbaproject.bin", ".exe", ".dll")):
                        raise forms.ValidationError("El archivo contiene contenido no permitido.")
                    total += member.file_size
                if total > settings.SOAT_MAX_UPLOAD_BYTES * 20:
                    raise forms.ValidationError("El archivo comprimido excede el límite seguro.")
        except BadZipFile as exc:
            raise forms.ValidationError("El archivo no corresponde a un Excel válido.") from exc
        finally:
            uploaded.seek(0)

        try:
            hoja = legacy.resolver_hoja(uploaded, "Sheet0")
            uploaded.seek(0)
            legacy.detectar_fila_encabezado(uploaded, hoja, requiere_ramo=True)
        except (KeyError, ValueError, OSError) as exc:
            raise forms.ValidationError(
                "El archivo no tiene la estructura de un reporte SOAT de Zoho "
                "(deben existir las columnas 'Placa' y 'Ramo (Póliza)')."
            ) from exc
        finally:
            uploaded.seek(0)

        return uploaded
