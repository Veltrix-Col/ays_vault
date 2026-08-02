from __future__ import annotations

import logging
import time
import uuid

from django.http import Http404
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from .forms import CompanySearchForm, PersonSearchForm
from .services import CompanySearchService, EntityDetailService, PersonSearchService
from .services.common import ColectivosServiceError
from .zoho import get_colectivos_environment


logger = logging.getLogger("cotizacion_colectivos")


def _environment_context():
    return {"zoho_environment": get_colectivos_environment()}


def _error_status(exc):
    if exc.code == "permission":
        return 403
    if exc.code in {"invalid_record", "not_found"}:
        return 404
    return 503


@never_cache
@require_http_methods(["GET"])
def index(request):
    return render(request, "cotizacion_colectivos/index.html", {
        "company_form": CompanySearchForm(auto_id="id_company_%s"),
        "person_form": PersonSearchForm(auto_id="id_person_%s"),
        **_environment_context(),
    })


def _search(request, *, form_class, service_class, entity_kind):
    environment = get_colectivos_environment()
    form = form_class(request.POST or None)
    results, error, status = None, "", 200
    if request.method == "POST" and form.is_valid():
        started = time.monotonic()
        correlation = uuid.uuid4().hex
        error_category = "none"
        try:
            query = form.cleaned_data["query"]
            results = service_class().search(query)
            if query.isdigit():
                form = form_class()
        except ColectivosServiceError as exc:
            error_category = exc.code
            error, status = exc.message, _error_status(exc)
        except Exception:
            error_category = "unknown"
            error = f"{environment['label']} no está disponible temporalmente. Intente nuevamente más tarde."
            status = 503
        logger.info(
            "colectivos_search application=cotizacion_colectivos entity=%s operation=search duration_ms=%d results=%d error=%s user_id=%s profile=%s correlation=%s",
            entity_kind,
            round((time.monotonic() - started) * 1000),
            len(results or ()),
            error_category,
            request.user.pk,
            environment["profile"],
            correlation,
        )
    return render(request, "cotizacion_colectivos/search.html", {
        "form": form, "results": results, "error": error, "entity_kind": entity_kind,
        "zoho_environment": environment,
    }, status=status)


@never_cache
@require_http_methods(["GET", "POST"])
def company_search(request):
    return _search(request, form_class=CompanySearchForm, service_class=CompanySearchService, entity_kind="company")


@never_cache
@require_http_methods(["GET", "POST"])
def person_search(request):
    return _search(request, form_class=PersonSearchForm, service_class=PersonSearchService, entity_kind="person")


def _detail(request, token, *, method, entity_kind):
    environment = get_colectivos_environment()
    started = time.monotonic()
    correlation = uuid.uuid4().hex
    error_category = "none"
    try:
        detail = getattr(EntityDetailService(), method)(token)
    except ColectivosServiceError as exc:
        error_category = exc.code
        if exc.code in {"invalid_record", "not_found"}:
            _log_detail(request, entity_kind, environment, started, error_category, correlation, 0)
            raise Http404("Registro no encontrado") from exc
        _log_detail(request, entity_kind, environment, started, error_category, correlation, 0)
        return render(request, "cotizacion_colectivos/detail_error.html", {
            "message": exc.message, "zoho_environment": environment,
        }, status=_error_status(exc))
    except Exception:
        _log_detail(request, entity_kind, environment, started, "unknown", correlation, 0)
        return render(
            request,
            "cotizacion_colectivos/detail_error.html",
            {"message": "No fue posible consultar la información relacionada. Intente nuevamente más tarde.", "zoho_environment": environment},
            status=503,
        )
    _log_detail(request, entity_kind, environment, started, error_category, correlation, 1)
    return render(request, "cotizacion_colectivos/detail.html", {
        "detail": detail, "entity_kind": entity_kind, "zoho_environment": environment,
    })


def _log_detail(request, entity_kind, environment, started, error_category, correlation, results):
    logger.info(
        "colectivos_detail application=cotizacion_colectivos entity=%s operation=detail duration_ms=%d results=%d error=%s user_id=%s profile=%s correlation=%s",
        entity_kind,
        round((time.monotonic() - started) * 1000),
        results,
        error_category,
        request.user.pk,
        environment["profile"],
        correlation,
    )


@never_cache
@require_http_methods(["GET"])
def company_detail(request, token):
    return _detail(request, token, method="company", entity_kind="company")


@never_cache
@require_http_methods(["GET"])
def person_detail(request, token):
    return _detail(request, token, method="person", entity_kind="person")
