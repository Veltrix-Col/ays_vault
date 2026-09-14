from django.urls import path

from . import views

app_name = "asistente_zoho"

urlpatterns = [
    path("mensaje/", views.mensaje, name="mensaje"),
]
