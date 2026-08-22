from __future__ import annotations

import logging
import uuid

from django.conf import settings
from django.http import HttpResponseForbidden, HttpResponseRedirect
from django.utils.module_loading import import_string

from .application_access import (
    ALLOWED_ACCESS_MODES,
    LOCAL_PUBLIC,
    TRUSTED_INTRANET,
    DelegatedAccessResult,
    inherited_application_for_path,
)


logger = logging.getLogger("application_access")


class TrustedIntranetAccessMiddleware:
    """Gate inherited-access tools without defining a token format prematurely."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        application = inherited_application_for_path(request.path_info)
        if application is None:
            return self.get_response(request)

        request.inherited_tool_application = application
        correlation = uuid.uuid4().hex
        mode = getattr(settings, "TOOLS_ACCESS_MODE", "")
        if application == "cotizacion_colectivos":
            if getattr(settings, "COLECTIVOS_INTERNAL_PUBLIC_ACCESS", False):
                request.colectivos_internal_public_access = True
                logger.info(
                    "tools_access result=accepted application=%s category=internal_public "
                    "correlation=%s",
                    application,
                    correlation,
                )
                return self._secured_response(request, application)
            if mode == LOCAL_PUBLIC:
                if (
                    request.user.is_authenticated
                    and (settings.DEBUG or getattr(settings, "RUNNING_TESTS", False))
                ):
                    return self._secured_response(request, application)
                logger.warning(
                    "tools_access result=rejected application=%s "
                    "category=internal_public_disabled correlation=%s",
                    application,
                    correlation,
                )
                return self._forbidden()
        if mode == LOCAL_PUBLIC and (
            settings.DEBUG or getattr(settings, "RUNNING_TESTS", False)
        ):
            return self._secured_response(request, application)

        if mode == TRUSTED_INTRANET:
            result = self._validate_delegated_access(request, application)
            if result.allowed:
                request.delegated_access = result
                logger.info(
                    "tools_access result=accepted application=%s category=%s correlation=%s",
                    application,
                    result.category,
                    correlation,
                )
                return self._secured_response(request, application)
            category = result.category
            if result.challenge_redirect:
                logger.info(
                    "tools_access result=challenge application=%s category=%s correlation=%s",
                    application,
                    category,
                    correlation,
                )
                return HttpResponseRedirect(result.challenge_redirect)
        elif mode not in ALLOWED_ACCESS_MODES:
            category = "invalid_access_mode"
        else:
            category = "local_public_outside_debug"

        logger.warning(
            "tools_access result=rejected application=%s category=%s correlation=%s",
            application,
            category,
            correlation,
        )
        return self._forbidden()

    def _secured_response(self, request, application):
        response = self.get_response(request)
        response.setdefault("Cache-Control", "no-store, private")
        response.setdefault("Pragma", "no-cache")
        return response

    @staticmethod
    def _validate_delegated_access(request, application: str) -> DelegatedAccessResult:
        validator_path = getattr(settings, "TOOLS_DELEGATED_ACCESS_VALIDATOR", "").strip()
        if not validator_path:
            return DelegatedAccessResult(False, "validator_not_configured")
        try:
            validator = import_string(validator_path)
            result = validator(request=request, application=application)
        except Exception:
            return DelegatedAccessResult(False, "validator_error")
        if not isinstance(result, DelegatedAccessResult):
            return DelegatedAccessResult(False, "invalid_validator_result")
        return result

    @staticmethod
    def _forbidden():
        return HttpResponseForbidden(
            "No fue posible validar el acceso desde la intranet autorizada."
        )
