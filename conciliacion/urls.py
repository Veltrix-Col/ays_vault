from django.urls import path

from .views import actualizar_credito, prellenar_cobro, upload

app_name = "conciliacion"

urlpatterns = [
    path("", upload, name="index"),
    path("cobros/prellenar/", prellenar_cobro, name="prellenar_cobro"),
    path("riesgos/actualizar-credito/", actualizar_credito, name="actualizar_credito"),
]
