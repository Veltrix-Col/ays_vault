from django.urls import path

from .views import prellenar_cobro, upload

app_name = "conciliacion"

urlpatterns = [
    path("", upload, name="index"),
    path("cobros/prellenar/", prellenar_cobro, name="prellenar_cobro"),
]
