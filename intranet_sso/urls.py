from django.urls import path

from .views import callback

app_name = "intranet_sso"

urlpatterns = [
    path("callback/", callback, name="callback"),
]
