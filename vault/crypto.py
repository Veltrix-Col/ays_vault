import base64, hashlib
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings

def _key():
    raw=settings.FIELD_ENCRYPTION_KEY
    if raw: return raw.encode()
    return base64.urlsafe_b64encode(hashlib.sha256(settings.SECRET_KEY.encode()).digest())
_cipher=Fernet(_key())
def encrypt(value): return _cipher.encrypt(value.encode()).decode() if value else ''
def decrypt(value):
    if not value:return ''
    try:return _cipher.decrypt(value.encode()).decode()
    except InvalidToken: raise ValueError('No fue posible descifrar el dato con la llave actual.')
