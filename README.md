# A&S Vault

**Custodia segura de información sensible**

A&S Vault es una bóveda interna Django para reemplazar el archivo operativo de medios de pago mediante cifrado, separación de funciones, MFA, sesiones controladas y auditoría verificable. No es un CRUD genérico ni una certificación PCI DSS.

> **No use datos reales todavía.** Siguen siendo obligatorios un KMS/Key Vault, PostgreSQL probado bajo concurrencia, auditoría externa inmutable, infraestructura productiva y pruebas de penetración.

## Controles implementados

- Roles Administrador, Líder de cartera y Analista, validados en backend.
- El Administrador no tiene acceso a tarjetas. Solo el Líder crea, edita y desactiva; el Analista consulta únicamente activas.
- PAN y vencimiento cifrados con Fernet; detección de duplicados mediante HMAC independiente, Luhn y franquicia.
- MFA TOTP obligatorio mediante `django-otp`, compatible con aplicaciones autenticadoras estándar.
- Enrolamiento con contraseña, QR generado en memoria, clave manual mostrada solo durante el flujo y primer TOTP obligatorio.
- Diez códigos de recuperación almacenados exclusivamente mediante hash, de un solo uso y regenerables tras reautenticación.
- Login en dos etapas: la contraseña correcta no crea una sesión autenticada hasta validar MFA o recuperación.
- Una sesión activa por usuario, identificador por hash, session key cifrada para revocación real y expiración tras 10 minutos de inactividad.
- Registro prudente de navegador, sistema, tipo de dispositivo e IP; estados Nuevo, Reconocido, Bloqueado y Revocado.
- Reautenticación de 5 minutos ligada a usuario, sesión y propósito para operaciones críticas.
- Alertas persistentes para dispositivo/IP nueva, MFA/reautenticación fallidos, recuperación, reinicio MFA, sesiones reemplazadas, bloqueos y cambios sensibles.
- Auditoría secuencial con hash encadenado y verificación mediante `verify_audit_chain`.
- CSP, Permissions Policy, no-cache, CSRF, Axes y endurecimiento HTTPS condicionado al entorno.

## Instalación local

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
python -c "import secrets; print(secrets.token_urlsafe(48))"
python manage.py migrate
python manage.py seed_demo
python manage.py check
python manage.py test
python manage.py verify_audit_chain
```

Configure valores independientes en `FIELD_ENCRYPTION_KEY` y `FIELD_FINGERPRINT_KEY`. SQLite es únicamente para desarrollo; con `DEBUG=False` se exige PostgreSQL.

## Dependencias

Las versiones están fijadas en `requirements.txt`. MFA usa `django-otp==1.7.0`; Segno genera el QR sin archivos temporales. `pip-audit==2.10.1` está separado en `requirements-dev.txt` y no es una dependencia productiva.

La auditoría inicial detectó avisos en Django 5.2.15, cryptography 45.0.5 y python-dotenv 1.1.1. Se actualizaron respectivamente a 5.2.16, 48.0.1 y 1.2.2, se repitieron todas las pruebas y el resultado final fue `No known vulnerabilities found`.

## Datos demo

`python manage.py seed_demo` crea idempotentemente 30 tarjetas ficticias y tres usuarios individuales: `admin.seguridad`, `laura.cartera` y `andres.analista`, con contraseña local inicial `DemoSeguro2026!`. Todos deben enrolar MFA antes de ingresar. Cambie o elimine estas credenciales antes de cualquier despliegue.

## Operación de seguridad

- El usuario administra sus sesiones y dispositivos desde las interfaces propias.
- El Administrador consulta identidades, revoca sesiones, desbloquea dispositivos y reinicia MFA sin ver secretos ni adquirir acceso a tarjetas.
- Reiniciar MFA elimina dispositivos TOTP y códigos, revoca sesiones/autorizaciones/revelados y obliga nuevo enrolamiento.
- Reconocer un dispositivo solo reduce alertas; nunca evita MFA, permisos, horario, reautenticación o revelado.
- Los códigos de recuperación solo se muestran al generarse. No se guardan en archivos ni se envían por correo.

Consulte [docs/MFA_AND_SESSION_SECURITY.md](docs/MFA_AND_SESSION_SECURITY.md) y [docs/SECURITY_ARCHITECTURE.md](docs/SECURITY_ARCHITECTURE.md).

## Centro de Control y monitoreo operativo

El Administrador dispone de un Centro de Control separado de la operacion de tarjetas. Resume integridad de auditoria, MFA, base de datos, correo, configuracion, alertas, excepciones, adopcion y ejecuciones programadas. La linea de tiempo tiene filtros, paginacion y alcance backend por rol; solo usa ID interno o ultimos cuatro digitos de tarjeta.

Las politicas centrales administran horarios entre semana, sabado, domingo, zona `America/Bogota`, sesiones, reautenticacion, comportamiento fuera de horario e inactividad. Festivos y excepciones se resuelven localmente, sin API en tiempo real. Cargue los festivos nacionales con:

```powershell
python manage.py load_colombia_holidays --year 2026
python manage.py evaluate_security_policies --dry-run
python manage.py evaluate_security_policies
```

El comando de evaluacion es idempotente y revisa inactividad, usuarios sin uso/MFA, vencimientos, alertas vencidas e integridad. Puede ejecutarse desde cron o Task Scheduler y deja un registro seguro; la arquitectura permite moverlo a Celery sin cambiar las reglas.

El correo usa una capa intercambiable. Desarrollo usa el backend de Django/console. Produccion puede usar Microsoft Graph con OAuth 2.0 client credentials mediante MSAL y permiso de aplicacion `Mail.Send`; limite el acceso de la aplicacion al buzon remitente en Microsoft 365. El fallo de correo se registra, admite reintento y nunca revierte la operacion sensible que genero la alerta.

Documentacion operativa: [Centro de Control](docs/CONTROL_CENTER.md), [alertas y correo](docs/ALERTING_AND_EMAIL.md) y [politicas de acceso](docs/ACCESS_POLICIES.md).

## Variables nuevas

- `SESSION_INACTIVITY_SECONDS=600`
- `SESSION_ACTIVITY_THROTTLE_SECONDS=60`
- `REAUTH_TTL_SECONDS=300`
- `MFA_FAILURE_LIMIT=5`
- `MFA_ISSUER=A&S Vault`
- `ALERT_EMAIL_BACKEND=console|microsoft_graph`
- `ALERT_EMAIL_FROM`, `ALERT_EMAIL_ADMIN`, `ALERT_EMAIL_LEADER`
- `MS_GRAPH_TENANT_ID`, `MS_GRAPH_CLIENT_ID`, `MS_GRAPH_CLIENT_SECRET`, `MS_GRAPH_SENDER`
- `EMAIL_TIMEOUT_SECONDS`, `EMAIL_MAX_RETRIES`, `VAULT_BASE_URL`

## Limitaciones y riesgos pendientes

Faltan KMS/Key Vault, rotación operativa de llaves, SIEM/log externo inmutable, pruebas PostgreSQL y de concurrencia, pentest, QA visual completo, VPN/allowlist, backups cifrados probados y revisión formal de cumplimiento. El restablecimiento de contraseña por correo no se habilitó: antes requiere diseño anti-enumeración y recuperación organizacional controlada.

### Requerimiento empresarial adicional pendiente

Existe un requerimiento relacionado con un código adicional de seguridad asociado a las tarjetas. **No fue implementado**: no existen modelo, migración, formulario, persistencia, demo, revelado ni copia. Requiere concepto formal de seguridad/cumplimiento y decisión arquitectónica antes de cualquier desarrollo.
