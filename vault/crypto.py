import hashlib
import hmac

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def _encryption_key():
    raw = settings.FIELD_ENCRYPTION_KEY
    if raw:
        return raw.encode()
    raise ImproperlyConfigured("FIELD_ENCRYPTION_KEY requerida en todos los entornos")


def _fingerprint_key():
    raw = getattr(settings, "FIELD_FINGERPRINT_KEY", "")
    if raw:
        return raw.encode()
    if settings.APP_ENV == "development":
        return hashlib.sha256(_encryption_key() + b":fingerprint").digest()
    raise ImproperlyConfigured("FIELD_FINGERPRINT_KEY requerida")


def encrypt(value):
    return Fernet(_encryption_key()).encrypt(value.encode()).decode() if value else ""


def decrypt(value):
    if not value:
        return ""
    try:
        return Fernet(_encryption_key()).decrypt(value.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("No fue posible descifrar el dato con la llave configurada.") from exc


def fingerprint(value):
    normalized = "".join(character for character in value if character.isdigit())
    return hmac.new(_fingerprint_key(), normalized.encode(), hashlib.sha256).hexdigest()
