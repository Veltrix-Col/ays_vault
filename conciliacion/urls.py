from django.urls import path

from .views import upload

app_name = "conciliacion"

urlpatterns = [
    path("", upload, name="index"),
]
