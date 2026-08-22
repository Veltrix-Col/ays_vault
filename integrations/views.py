from __future__ import annotations

import hashlib
import logging
import secrets
import time

from django.conf import settings
from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .decorators import secure_integration_view
from .permissions import zoho_setup_access
from .zoho.exceptions import ZohoError
from .zoho.services import ZohoServices
from .zoho.settings import ALLOWED_PROFILES, ZohoSettings, normalize_profile
from .zoho.token_store import get_token_store

logger = logging.getLogger("integrations.zoho")
STATE_SESSION_KEY = "zoho_oauth_state"
DELIVERY_PROFILE_SESSION_KEY = "zoho_refresh_delivery_profile"
STATE_MAX_AGE_SECONDS = 600


def _state_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _ensure_session_key(request: HttpRequest) -> str:
    if request.session.session_key is None:
        request.session.save()
    return str(request.session.session_key)


def _actor_id(request: HttpRequest) -> str:
    return str(request.user.pk) if request.user.is_authenticated else "anonymous"


def _refresh_token_delivery_enabled(config: ZohoSettings) -> bool:
    return bool(
        settings.DEBUG
        and config.enabled
        and config.public_setup_enabled
    )


def _valid_state(
    expected: object,
    supplied: str,
    *,
    session_key: str,
    user_id: int | None,
) -> bool:
    if not isinstance(expected, dict) or not supplied:
        return False
    try:
        age = time.time() - float(expected.get("created_at") or 0)
    except (TypeError, ValueError, OverflowError):
        return False
    profile = str(expected.get("profile") or "")
    return (
        profile in ALLOWED_PROFILES
        and expected.get("user_id") == user_id
        and secrets.compare_digest(
            str(expected.get("session_digest") or ""),
            _state_digest(session_key),
        )
        and 0 <= age <= STATE_MAX_AGE_SECONDS
        and secrets.compare_digest(
            str(expected.get("digest") or ""), _state_digest(supplied)
        )
        and secrets.compare_digest(
            str(expected.get("profile_digest") or ""),
            _state_digest(f"{supplied}:{profile}"),
        )
    )


def _enabled_profiles() -> list[ZohoSettings]:
    profiles = []
    for profile in ALLOWED_PROFILES:
        config = ZohoSettings.from_django(profile)
        if config.enabled:
            profiles.append(config)
    return profiles


@require_POST
@secure_integration_view
@zoho_setup_access(public_methods={"POST"})
def zoho_connect(request: HttpRequest) -> HttpResponse:
    try:
        profile = normalize_profile(request.POST.get("profile") or None)
        config = ZohoSettings.from_django(profile).validate()
        services = ZohoServices.build(config=config)
        state = services.oauth.generate_state()
        session_key = _ensure_session_key(request)
        user_id = request.user.pk if request.user.is_authenticated else None
        request.session[STATE_SESSION_KEY] = {
            "digest": _state_digest(state),
            "profile_digest": _state_digest(f"{state}:{profile}"),
            "profile": profile,
            "created_at": time.time(),
            "session_digest": _state_digest(session_key),
            "user_id": user_id,
        }
        request.session.modified = True
        target = services.oauth.authorization_url(state)
    except ZohoError as exc:
        request.session.pop(STATE_SESSION_KEY, None)
        logger.warning(
            "Inicio OAuth Zoho rechazado categoria=%s usuario_id=%s",
            exc.category,
            _actor_id(request),
        )
        messages.error(request, str(exc))
        return redirect("integrations:zoho_status")
    logger.info(
        "Inicio OAuth Zoho perfil=%s usuario_id=%s",
        profile,
        _actor_id(request),
    )
    return redirect(target)


@require_GET
@secure_integration_view
@zoho_setup_access(public_methods={"GET"})
def zoho_callback(request: HttpRequest) -> HttpResponse:
    expected = request.session.pop(STATE_SESSION_KEY, None)
    supplied = request.GET.get("state", "")
    session_key = _ensure_session_key(request)
    user_id = request.user.pk if request.user.is_authenticated else None
    valid_state = _valid_state(
        expected,
        supplied,
        session_key=session_key,
        user_id=user_id,
    )
    if not valid_state:
        logger.warning(
            "Callback OAuth Zoho con state inválido usuario_id=%s",
            _actor_id(request),
        )
        messages.error(
            request,
            "La autorización de Zoho venció o no pudo validarse. Iníciela nuevamente.",
        )
        return redirect("integrations:zoho_status")
    if request.GET.get("error"):
        logger.warning(
            "Zoho rechazó consentimiento usuario_id=%s", _actor_id(request)
        )
        messages.error(request, "Zoho no autorizó la conexión solicitada.")
        return redirect("integrations:zoho_status")
    code = request.GET.get("code", "")
    profile = str(expected.get("profile")) if isinstance(expected, dict) else ""
    oauth = None
    config = None
    session_binding = _state_digest(session_key)
    try:
        config = ZohoSettings.from_django(profile).validate()
        oauth = ZohoServices.build(config=config).oauth
        oauth.exchange_code(code)
        received_refresh_token = oauth.consume_received_refresh_token()
        if (
            isinstance(received_refresh_token, str)
            and received_refresh_token
            and _refresh_token_delivery_enabled(config)
        ):
            get_token_store(config=config).stage_refresh_token_delivery(
                session_binding,
                received_refresh_token,
            )
            request.session[DELIVERY_PROFILE_SESSION_KEY] = profile
            request.session.modified = True
    except ZohoError as exc:
        request.session.pop(DELIVERY_PROFILE_SESSION_KEY, None)
        if oauth is not None:
            discard = getattr(oauth, "discard_candidate_tokens", None)
            if callable(discard):
                discard()
        if config is not None:
            get_token_store(config=config).discard_refresh_token_delivery(
                session_binding
            )
        request.session.modified = True
        logger.warning(
            "Callback OAuth Zoho fallido categoria=%s usuario_id=%s",
            exc.category,
            _actor_id(request),
        )
        messages.error(request, str(exc))
    else:
        logger.info(
            "OAuth Zoho autorizado perfil=%s usuario_id=%s",
            profile,
            _actor_id(request),
        )
        messages.success(
            request,
            "Zoho fue autorizado. El token de renovación permanece solo en memoria "
            "hasta configurar el secreto del entorno.",
        )
    return redirect("integrations:zoho_status")


@require_http_methods(["GET", "POST"])
@secure_integration_view
@zoho_setup_access(public_methods={"GET", "POST"})
def zoho_status(request: HttpRequest) -> HttpResponse:
    organization = None
    error = ""
    config = None
    configured = False
    valid_configuration = False
    refresh_present = False
    refresh_token_delivery = ""
    refresh_token_delivery_profile = ""
    refresh_token_delivery_environment = ""
    enabled_profiles = []
    try:
        config = ZohoSettings.from_django()
        enabled_profiles = _enabled_profiles()
        configured = config.configured
        if config.enabled and configured:
            try:
                config.validate()
                valid_configuration = True
            except ZohoError:
                valid_configuration = False
        refresh_present = bool(get_token_store(config=config).get_refresh_token())
        if (
            request.method == "GET"
            and request.session.session_key
        ):
            delivery_profile = request.session.pop(
                DELIVERY_PROFILE_SESSION_KEY,
                "",
            )
            if delivery_profile:
                delivery_config = ZohoSettings.from_django(delivery_profile)
                if _refresh_token_delivery_enabled(delivery_config):
                    refresh_token_delivery = (
                        get_token_store(config=delivery_config)
                        .consume_refresh_token_delivery(
                            _state_digest(str(request.session.session_key))
                        )
                    )
                    if refresh_token_delivery:
                        refresh_token_delivery_profile = delivery_config.profile
                        refresh_token_delivery_environment = (
                            delivery_config.environment
                        )
                else:
                    get_token_store(
                        config=delivery_config
                    ).consume_refresh_token_delivery(
                        _state_digest(str(request.session.session_key))
                    )
        if request.method == "POST":
            if request.POST.get("action") != "check":
                messages.error(request, "La acción solicitada no es válida.")
                return redirect("integrations:zoho_status")
            requested_profile = normalize_profile(
                request.POST.get("profile") or None
            )
            requested_config = ZohoSettings.from_django(
                requested_profile
            ).validate(require_refresh_token=True)
            requested_refresh_present = bool(
                get_token_store(config=requested_config).get_refresh_token()
            )
            if (
                requested_config.public_setup_enabled
                and not requested_refresh_present
            ):
                messages.error(
                    request,
                    "No existe una autorización disponible para probar la conexión.",
                )
                return redirect("integrations:zoho_status")
            organization = ZohoServices.build(
                config=requested_config
            ).metadata.organization()
            if (
                getattr(organization, "environment", "")
                and getattr(organization, "environment")
                != requested_config.environment
            ):
                from .zoho.exceptions import ZohoConfigurationError

                raise ZohoConfigurationError(
                    "El entorno reportado por Zoho no coincide con el perfil solicitado."
                )
            messages.success(request, "La conexión de solo lectura con Zoho respondió correctamente.")
    except ZohoError as exc:
        error = str(exc)
        logger.warning(
            "Estado Zoho fallido categoria=%s usuario_id=%s",
            exc.category,
            _actor_id(request),
        )
    except (TypeError, ValueError):
        config = None
        configured = False
        valid_configuration = False
        error = "La configuración de Zoho contiene valores inválidos."

    context = {
        "zoho": {
            "enabled": bool(config and config.enabled),
            "profile": config.profile if config else "No configurado",
            "environment": config.environment if config else "No configurado",
            "configured": configured,
            "valid_configuration": valid_configuration,
            "public_setup_enabled": bool(
                config and config.public_setup_enabled
            ),
            "refresh_present": refresh_present,
            "client_id_present": bool(config and config.client_id),
            "client_secret_present": bool(config and config.client_secret),
            "data_center": config.data_center if config else "No configurado",
            "redirect_uri": config.redirect_uri if config else "No configurado",
            "read_only": True,
            "connection_status": (
                "Deshabilitada"
                if not (config and config.enabled)
                else (
                    "Configuración incompleta"
                    if not valid_configuration
                    else (
                        "Autorización disponible"
                        if refresh_present
                        else "Autorización pendiente"
                    )
                )
            ),
        },
        "organization": organization,
        "connection_error": error,
        "refresh_token_delivery": refresh_token_delivery,
        "refresh_token_delivery_profile": refresh_token_delivery_profile,
        "refresh_token_delivery_environment": refresh_token_delivery_environment,
        "enabled_profiles": [
            {
                "name": item.profile,
                "environment": item.environment,
                "configured": item.configured,
                "refresh_present": bool(
                    get_token_store(config=item).get_refresh_token()
                ),
            }
            for item in enabled_profiles
        ],
    }
    return render(request, "integrations/zoho/status.html", context)
