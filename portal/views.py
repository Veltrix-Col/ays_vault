from django.shortcuts import render

from .catalog import application_catalog


def public_home(request):
    return render(request, "portal/home.html", {"applications": application_catalog()})
