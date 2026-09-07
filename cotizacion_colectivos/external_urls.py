from django.urls import path

from . import external_views

app_name = "colectivos_external"
urlpatterns = [
    path("cotizacion-individual/confirmacion/<str:token>/", external_views.individual_confirmation, name="individual_confirmation"),
    path("cotizacion-individual/<str:token>/verificar/", external_views.individual_verify, name="individual_verify"),
    path("cotizacion-individual/<str:token>/", external_views.individual_quotation, name="individual_quotation"),
    path("portal/", external_views.portal, name="portal"),
    path("portal/guardar/", external_views.save_draft, name="save_draft"),
    path("portal/novedad/editar/", external_views.edit_draft_change, name="edit_draft_change"),
    path("portal/novedad/quitar/", external_views.remove_draft_change, name="remove_draft_change"),
    path("portal/enviar/", external_views.submit, name="submit"),
    path("portal/adjuntos/", external_views.upload_attachment, name="upload_attachment"),
    path("portal/excel/descargar/<int:attachment_id>/", external_views.download_imported_excel, name="download_imported_excel"),
    path("portal/adjuntos/descargar/<int:attachment_id>/", external_views.download_external_attachment, name="download_external_attachment"),
    path("portal/excel/plantilla/", external_views.download_template, name="download_template"),
    path("portal/excel/cargar/", external_views.upload_excel, name="upload_excel"),
    path("portal/excel/quitar/", external_views.remove_excel_import, name="remove_excel_import"),
    path("portal/excel/preview/<str:token>/", external_views.excel_preview, name="excel_preview"),
    path("portal/excel/preview/<str:token>/confirmar/", external_views.confirm_excel_preview, name="confirm_excel_preview"),
    path("portal/excel/preview/<str:token>/cancelar/", external_views.cancel_excel_preview, name="cancel_excel_preview"),
    path("sin-novedades/<str:token>/", external_views.no_changes_entry, name="no_changes_entry"),
    path("sin-novedades/<str:token>/confirmar/", external_views.no_changes_confirm, name="no_changes_confirm"),
    path("<str:token>/verificar/", external_views.verify, name="verify"),
    path("<str:token>/", external_views.entry, name="entry"),
]
