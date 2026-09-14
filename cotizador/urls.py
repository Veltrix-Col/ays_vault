from django.urls import path

from .views import upload

app_name = "cotizador"

urlpatterns = [
    path("", upload, name="index"),
]
