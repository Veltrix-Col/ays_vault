from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import jwt
from django.conf import settings
from django.db import IntegrityError
from django.utils import timezone

from .models import ConsumedAssertion


class IntranetAssertionError(Exception):
    category = "invalid_token"


class InvalidAssertion(IntranetAssertionError):
    category = "invalid_token"


class ExpiredAssertion(IntranetAssertionError):
    category = "expired_token"


class WrongAudienceAssertion(IntranetAssertionError):
    category = "wrong_audience"


class ReplayedAssertion(IntranetAssertionError):
    category = "invalid_token"


@dataclass(frozen=True)
class IntranetIdentity:
    subject: str


def _public_key() -> str:
    key = getattr(settings, "INTRANET_SSO_PUBLIC_KEY", "").strip()
    if not key:
        raise InvalidAssertion("INTRANET_SSO_PUBLIC_KEY no configurada")
    return key


def verify_intranet_assertion(token: str) -> IntranetIdentity:
    """Verifica un JWT RS256 emitido por la intranet y consume su jti una sola vez."""
    try:
        claims = jwt.decode(
            token,
            _public_key(),
            algorithms=["RS256"],
            audience=settings.INTRANET_SSO_AUDIENCE,
            issuer=settings.INTRANET_SSO_ISSUER,
            options={"require": ["exp", "iat", "sub", "aud", "iss", "jti"]},
            leeway=0,
        )
    except jwt.ExpiredSignatureError as exc:
        raise ExpiredAssertion(str(exc)) from exc
    except jwt.InvalidAudienceError as exc:
        raise WrongAudienceAssertion(str(exc)) from exc
    except jwt.InvalidTokenError as exc:
        raise InvalidAssertion(str(exc)) from exc

    max_age = getattr(settings, "INTRANET_SSO_ASSERTION_MAX_AGE", 60)
    if claims["exp"] - claims["iat"] > max_age:
        raise InvalidAssertion("La aserción declara una vigencia mayor a la permitida")

    subject = str(claims["sub"]).strip()
    if not subject:
        raise InvalidAssertion("La aserción no incluye un sujeto válido")

    _consume_once(str(claims["jti"]), subject, claims["exp"])
    return IntranetIdentity(subject=subject)


def _consume_once(jti: str, subject: str, exp_timestamp: float) -> None:
    ConsumedAssertion.objects.filter(expires_at__lt=timezone.now()).delete()
    try:
        ConsumedAssertion.objects.create(
            jti=jti,
            subject=subject,
            expires_at=dt.datetime.fromtimestamp(exp_timestamp, tz=dt.timezone.utc),
        )
    except IntegrityError as exc:
        raise ReplayedAssertion("Esta aserción ya fue utilizada") from exc
