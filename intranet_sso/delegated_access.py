from __future__ import annotations

from urllib.parse import urlencode

from django.conf import settings
from django.core import signing
from django.urls import reverse

from config.application_access import DelegatedAccessResult

COOKIE_NAME_SETTING = "INTRANET_SSO_COOKIE_NAME"
SESSION_SALT = "intranet_sso.local_session"


def _cookie_name() -> str:
    return getattr(settings, COOKIE_NAME_SETTING, "intranet_sso")


def _session_max_age() -> int:
    return getattr(settings, "INTRANET_SSO_SESSION_MAX_AGE", 2700)


def issue_local_session(response, *, subject: str) -> None:
    """Emite, tras validar la aserción de WordPress, la cookie propia del banco de herramientas."""
    value = signing.dumps({"sub": subject, "aud": settings.INTRANET_SSO_AUDIENCE}, salt=SESSION_SALT)
    response.set_cookie(
        _cookie_name(),
        value,
        max_age=_session_max_age(),
        httponly=True,
        secure=not settings.DEBUG,
        samesite="Lax",
    )


def validate_intranet_session(*, request, application: str) -> DelegatedAccessResult:
    """Validador real para TOOLS_DELEGATED_ACCESS_VALIDATOR: revisa la cookie local ya emitida.

    Ausencia de cookie o expiración natural revalidan en silencio contra la intranet
    (redirect invisible); una firma inválida o una audiencia incorrecta sí cortan en seco,
    porque indican manipulación en vez de simple paso del tiempo.
    """
    raw = request.COOKIES.get(_cookie_name())
    if not raw:
        return DelegatedAccessResult(False, "no_session", challenge_redirect=_challenge_url(request))
    try:
        payload = signing.loads(raw, salt=SESSION_SALT, max_age=_session_max_age())
    except signing.SignatureExpired:
        return DelegatedAccessResult(False, "expired_token", challenge_redirect=_challenge_url(request))
    except signing.BadSignature:
        return DelegatedAccessResult(False, "invalid_token")
    if not isinstance(payload, dict) or not payload.get("sub"):
        return DelegatedAccessResult(False, "invalid_token")
    if payload.get("aud") != settings.INTRANET_SSO_AUDIENCE:
        return DelegatedAccessResult(False, "wrong_audience")
    return DelegatedAccessResult(True, "validated", subject=payload["sub"])


def build_authorize_redirect_url(request, *, next_path: str) -> str:
    """Arma la URL de la intranet que inicia (o revalida) la sesión delegada."""
    callback_url = request.build_absolute_uri(reverse("intranet_sso:callback"))
    redirect_uri = f"{callback_url}?{urlencode({'next': next_path})}"
    return f"{settings.INTRANET_SSO_AUTHORIZE_URL}?{urlencode({'redirect_uri': redirect_uri})}"


def _challenge_url(request) -> str:
    return build_authorize_redirect_url(request, next_path=request.get_full_path())
