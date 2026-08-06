# Arquitectura técnica de CardManager

```text
Navegador
  → URLs y vistas `vault`
  → formularios/decoradores/middleware
  → servicios de seguridad, políticas, auditoría, alertas y reportes
  → Django ORM
  → SQLite (desarrollo) o PostgreSQL (no desarrollo)
```

## Componentes

- `vault/auth_views.py`, `identity.py`: contraseña, TOTP, recuperación y sesión.
- `vault/middleware.py`, `decorators.py`, `policies.py`: ejecución de controles.
- `vault/forms.py`, `views.py`, `urls.py`: interfaz y operaciones.
- `vault/crypto.py`, `security.py`, `sensitive_operations.py`: cifrado, fingerprints y autorizaciones temporales.
- `vault/models.py`: persistencia de negocio, seguridad, auditoría y operación.
- `vault/audit.py`, `alerts.py`, `notifications.py`, `tasks.py`: trazabilidad y avisos.
- `vault/reporting.py`: XLSX/PDF seguros.
- `templates/vault/`, `templates/registration/`, `static/`: presentación y branding.
- `vault/management/commands/`: operación administrativa.

## Administración y branding

`vault/admin.py` registra `User`, `UserProfile`, `AuditEvent` y `SecurityAlert`; varias entidades de control se registran en modo de solo lectura. La administración Django no reemplaza el centro de control y `PaymentCard` no se expone allí. Las pantallas operativas usan plantillas propias. Login/MFA reutilizan `templates/includes/cardmanager_auth_brand.html` y el activo oficial bajo `static/img/branding/cardmanager/`.

## Dependencias relevantes

Django 5.2, django-otp, django-axes, cryptography, segno, openpyxl, WeasyPrint, MSAL, httpx, WhiteNoise y psycopg. Zoho no participa en el flujo funcional de CardManager.

## Fronteras

El módulo usa autenticación de Django con capas propias; no delega decisiones de rol a JavaScript. Los valores protegidos no deben llegar a listados, logs ni exportes. Las plantillas consumen rutas Django y archivos estáticos locales.
