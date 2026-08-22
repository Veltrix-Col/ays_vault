from django.http import Http404
from django.shortcuts import render

from .catalog import application_catalog, area_catalog, get_area


def public_home(request):
    return render(request, "portal/home.html", {
        "applications": application_catalog(),
        "area_packages": area_catalog(),
    })


def area_home(request, area_slug):
    area = get_area(area_slug)
    if area is None:
        raise Http404("Área no disponible")
    return render(request, "portal/area_home.html", {"area": area})
