from django.urls import path

from .views import upload

app_name = "soat"

urlpatterns = [
    path("", upload, name="upload"),
]
