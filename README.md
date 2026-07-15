# A&S Vault

**Custodia segura de información sensible**

A&S Vault es una aplicación interna Django para sustituir el archivo operativo de tarjetas por una bóveda con cifrado, enmascaramiento, separación de funciones, revelado temporal y auditoría verificable. No es un CRUD genérico ni una certificación PCI DSS.

> **No use datos reales.** El sistema aún requiere MFA, gestión externa de llaves, infraestructura productiva y revisión formal de seguridad antes de operar información real.

## Arquitectura y controles actuales

- Roles funcionales: Administrador, Líder de cartera y Analista. Un perfil nuevo queda inactivo y sin rol.
- El Administrador no tiene rutas de tarjetas ni el modelo `PaymentCard` en Django Admin.
- Solo el Líder crea, edita y desactiva tarjetas; el Analista solo consulta tarjetas activas.
- PAN y vencimiento se cifran con Fernet. Un HMAC independiente permite detectar duplicados sin descifrar búsquedas.
- Validación Luhn y coherencia básica de franquicia antes de guardar.
- Revelado por campo con contraseña, motivo y autorización de copia de 25 segundos, ligada a usuario y sesión, de un solo uso.
- Auditoría con secuencia y hash encadenado; el valor sensible nunca se registra.
- Alertas persistentes para operaciones fuera de horario o fallidas.
- Sesión por inactividad de 10 minutos, cookies HttpOnly/SameSite, CSRF, CSP, Permissions Policy y endurecimiento HTTPS condicionado al entorno.
- Protección de fuerza bruta mediante `django-axes`.

La separación interna principal está en `vault/crypto.py`, `vault/security.py`, `vault/forms.py`, `vault/decorators.py` y `vault/views.py`. Consulte [docs/SECURITY_ARCHITECTURE.md](docs/SECURITY_ARCHITECTURE.md).

## Instalación local (PowerShell)

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
python -c "import secrets; print(secrets.token_urlsafe(48))"
python manage.py migrate
python manage.py seed_demo
python manage.py check
python manage.py test
python manage.py verify_audit_chain
python manage.py runserver
```

Pegue valores independientes en `FIELD_ENCRYPTION_KEY` y `FIELD_FINGERPRINT_KEY`. Mantenga `.env` fuera de Git.

## Variables de entorno

`APP_ENV`, `DEBUG`, `SECRET_KEY`, `ALLOWED_HOSTS`, `DB_ENGINE`, variables `DB_*`, `FIELD_ENCRYPTION_KEY`, `FIELD_FINGERPRINT_KEY`, `OFFICE_START`, `OFFICE_END`, `ALERT_EMAIL`, `DEFAULT_FROM_EMAIL` y `EMAIL_BACKEND`.

En producción use PostgreSQL (`DB_ENGINE=postgresql`), `DEBUG=False`, secretos externos y TLS. SQLite es solo para desarrollo.

## Datos demo

`python manage.py seed_demo` es idempotente. `--reset-demo` reinicia únicamente registros cuyo cliente empieza por `Cliente Demo `. Crea 30 tarjetas ficticias sin CVV y tres cuentas personales:

- `admin.seguridad`: Administrador.
- `laura.cartera`: Líder de cartera.
- `andres.analista`: Analista.

Contraseña local inicial: `DemoSeguro2026!`. Los correos usan `.invalid`. Cambie o elimine todas las credenciales antes de cualquier despliegue.

## Auditoría e integridad

```powershell
python manage.py verify_audit_chain
```

El hash encadenado detecta alteraciones, eliminaciones y discontinuidades, pero una cuenta con control total de base de datos y aplicación podría reconstruir la cadena. Producción requiere envío simultáneo a un registro externo inmutable/SIEM.

## Horario, correo y alertas

El MVP usa `America/Bogota`, `OFFICE_START` y `OFFICE_END`; fines de semana se consideran fuera de horario. La política actual permite y alerta. Festivos, excepciones, bloqueo/aprobación y gestión completa de estados de alerta siguen pendientes. Configure un backend SMTP real y `ALERT_EMAIL` fuera de desarrollo.

## MFA y producción

El modelo registra el estado futuro de MFA, pero **MFA todavía no está implementado ni debe marcarse manualmente como protección real**. La siguiente fase debe integrar una librería mantenida como `django-otp`, recuperación controlada y pruebas, sin OTP casero.

También están pendientes: KMS/Key Vault y rotación con recifrado, política robusta de sesión única y revocación remota, calendario de festivos/excepciones, gestión de alertas, métricas/riesgo avanzados, logs externos inmutables, backups cifrados, VPN/allowlist, EDR, monitoreo, pruebas de penetración y revisión de cumplimiento.

## Rotación de llaves

No cambie `FIELD_ENCRYPTION_KEY` directamente: volvería ilegibles los registros. La rotación debe usar una versión de llave, descifrar/recifrar por lotes dentro de transacciones auditadas y conservar temporalmente la llave anterior para reversión controlada. El HMAC de duplicados también debe recalcularse de forma coordinada al rotar `FIELD_FINGERPRINT_KEY`.
