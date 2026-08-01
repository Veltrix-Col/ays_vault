from django.urls import path

from . import views

app_name = "cotizacion_colectivos"
urlpatterns = [
    path("", views.index, name="index"),
    path("empresas/buscar/", views.company_search, name="company_search"),
    path("empresas/<str:token>/", views.company_detail, name="company_detail"),
    path("individuos/buscar/", views.person_search, name="person_search"),
    path("individuos/<str:token>/", views.person_detail, name="person_detail"),
]
