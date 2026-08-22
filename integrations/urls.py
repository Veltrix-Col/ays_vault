from django.urls import path

from . import views

app_name = "integrations"

urlpatterns = [
    path("zoho/connect/", views.zoho_connect, name="zoho_connect"),
    path("zoho/callback/", views.zoho_callback, name="zoho_callback"),
    path("zoho/status/", views.zoho_status, name="zoho_status"),
]
