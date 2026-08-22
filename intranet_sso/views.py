from __future__ import annotations

import logging
import uuid

from django.conf import settings
from django.http import HttpResponseBadRequest
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from .assertions import IntranetAssertionError, verify_intranet_assertion
from .delegated_access import issue_local_session

logger = logging.getLogger("intranet_sso")


def callback(request):
    correlation = uuid.uuid4().hex
    token = request.GET.get("sso_token", "")
    next_path = request.GET.get("next", "")

    if not url_has_allowed_host_and_scheme(next_path, allowed_hosts={request.get_host()}, require_https=not settings.DEBUG):
        next_path = reverse("public_home")

    if not token:
        logger.warning("intranet_sso result=rejected category=missing_token correlation=%s", correlation)
        response = HttpResponseBadRequest("Falta el token de la intranet.")
        response["Referrer-Policy"] = "no-referrer"
        return response

    try:
        identity = verify_intranet_assertion(token)
    except IntranetAssertionError as exc:
        logger.warning("intranet_sso result=rejected category=%s correlation=%s", exc.category, correlation)
        response = HttpResponseBadRequest("No fue posible validar la sesión de la intranet.")
        response["Referrer-Policy"] = "no-referrer"
        return response

    logger.info("intranet_sso result=accepted correlation=%s", correlation)
    response = redirect(next_path)
    # El sso_token viajó en esta URL: evita que sobreviva como Referer si el
    # navegador arrastra el referrer de este salto hacia next_path.
    response["Referrer-Policy"] = "no-referrer"
    issue_local_session(response, subject=identity.subject)
    return response
