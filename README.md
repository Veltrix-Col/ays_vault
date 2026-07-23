# A&S Vault

**Custodia segura de información sensible**

A&S Vault es una bóveda interna Django para reemplazar el archivo operativo de medios de pago mediante cifrado, separación de funciones, MFA, sesiones controladas y auditoría verificable. No es un CRUD genérico ni una certificación PCI DSS.

> **No use datos reales todavía.** Siguen siendo obligatorios un KMS/Key Vault, PostgreSQL probado bajo concurrencia, auditoría externa inmutable, infraestructura productiva y pruebas de penetración.

## Controles implementados

- Roles Administrador, Líder de cartera y Analista, validados en backend.
- El Administrador no accede a la Bóveda ni a endpoints protegidos. Solo el Líder crea, edita y desactiva; el Analista consulta únicamente tarjetas activas.
- Empresa, PAN y vencimiento cifrados con Fernet; detección de PAN duplicado mediante HMAC independiente, Luhn y franquicia.
- MFA TOTP obligatorio mediante `django-otp`, compatible con aplicaciones autenticadoras estándar.
- Enrolamiento con contraseña, QR generado en memoria, clave manual mostrada solo durante el flujo y primer TOTP obligatorio.
- Diez códigos de recuperación almacenados exclusivamente mediante hash, de un solo uso y regenerables tras reautenticación.
- Login en dos etapas: la contraseña correcta no crea una sesión autenticada hasta validar MFA o recuperación.
- Una sesión activa por usuario, identificador por hash, session key cifrada para revocación real y expiración tras 10 minutos de inactividad.
- Registro prudente de navegador, sistema, tipo de dispositivo e IP; estados Nuevo, Reconocido, Bloqueado y Revocado.
- Reautenticación por propósito para administración y verificación reforzada de identidad durante 15 minutos, ligada a usuario y sesión. Cada operación protegida exige un motivo y una referencia nuevos, vinculados a una tarjeta concreta; sus campos comparten ese contexto y cada revelado permanece visible 20 segundos.
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

- Líder y Analista trabajan exclusivamente en la Bóveda; sesiones, dispositivos, alertas, auditoría e informes son de acceso administrativo.
- El Administrador consulta identidades, revoca sesiones, desbloquea dispositivos y reinicia MFA sin ver secretos ni adquirir acceso a valores protegidos.
- Reiniciar MFA elimina dispositivos TOTP y códigos, revoca sesiones/autorizaciones/revelados y obliga nuevo enrolamiento.
- Reconocer un dispositivo solo reduce alertas; nunca evita MFA, permisos, horario, reautenticación o revelado.
- Los códigos de recuperación solo se muestran al generarse. No se guardan en archivos ni se envían por correo.

Consulte [docs/MFA_AND_SESSION_SECURITY.md](docs/MFA_AND_SESSION_SECURITY.md) y [docs/SECURITY_ARCHITECTURE.md](docs/SECURITY_ARCHITECTURE.md).

## Centro de Control y monitoreo operativo

El Administrador dispone de un Centro de Control separado de la operación de tarjetas. Resume integridad de auditoría, MFA, base de datos, correo, configuración, alertas, excepciones, adopción y ejecuciones programadas. La línea de tiempo es exclusivamente administrativa y solo identifica tarjetas mediante ID interno o últimos cuatro dígitos.

Las politicas centrales administran horarios entre semana, sabado, domingo, zona `America/Bogota`, sesiones, reautenticacion, comportamiento fuera de horario e inactividad. Festivos y excepciones se resuelven localmente, sin API en tiempo real. Cargue los festivos nacionales con:

```powershell
python manage.py load_colombia_holidays --year 2026
python manage.py evaluate_security_policies --dry-run
python manage.py evaluate_security_policies
```

El comando de evaluacion es idempotente y revisa inactividad, usuarios sin uso/MFA, vencimientos, alertas vencidas e integridad. Puede ejecutarse desde cron o Task Scheduler y deja un registro seguro; la arquitectura permite moverlo a Celery sin cambiar las reglas.

El correo usa una capa intercambiable. Desarrollo usa consola; la puesta en funcionamiento inicial puede usar SMTP de Microsoft 365 con TLS y contraseña de aplicación recibida solo por variable de entorno; Microsoft Graph con OAuth 2.0 permanece disponible como alternativa. El fallo de correo se registra con un código seguro, admite reintentos limitados y nunca revierte la operación sensible que originó la alerta.

Documentacion operativa: [Centro de Control](docs/CONTROL_CENTER.md), [alertas y correo](docs/ALERTING_AND_EMAIL.md), [politicas de acceso](docs/ACCESS_POLICIES.md) e [informes y exportaciones](docs/REPORTING_AND_EXPORTS.md).

## Informes seguros

La Línea de Tiempo dispone de filtros rápidos, cuadrícula principal, panel avanzado, chips removibles, orden y paginación de 25/50/100 filas. El Centro de Informes es exclusivo del Administrador y ofrece Línea de Tiempo, Alertas, Accesos, Adopción, Tarjetas Seguras y Salud Operativa. El inventario de tarjetas excluye Empresa, PAN y vencimiento.

Excel se genera con `openpyxl==3.1.5`, sin macros ni formulas procedentes de datos. PDF se genera en memoria con `WeasyPrint==69.0`; no se crean archivos publicos ni temporales persistentes. Toda generacion exige POST con CSRF, registra resultado y filtros seguros en `ReportExport`, y crea un evento `REPORT_EXPORT` en la cadena de auditoria. Los valores predeterminados son 5.000 filas para Excel, 1.000 para PDF y 90 dias por consulta.

## Variables nuevas

- `SESSION_INACTIVITY_SECONDS=600`
- `SESSION_ACTIVITY_THROTTLE_SECONDS=60`
- `REAUTH_TTL_SECONDS=300`
- `MFA_FAILURE_LIMIT=5`
- `MFA_ISSUER=A&S Vault`
- `ALERT_EMAIL_BACKEND=console|smtp|graph`
- `EMAIL_BACKEND`, `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_USE_TLS`, `EMAIL_USE_SSL`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `DEFAULT_FROM_EMAIL`
- `ALERT_EMAIL_FROM`, `ALERT_EMAIL_ADMIN`, `ALERT_EMAIL_LEADER`
- `MS_GRAPH_TENANT_ID`, `MS_GRAPH_CLIENT_ID`, `MS_GRAPH_CLIENT_SECRET`, `MS_GRAPH_SENDER`
- `EMAIL_TIMEOUT_SECONDS`, `EMAIL_MAX_RETRIES`, `VAULT_BASE_URL`

Consulte [Configuración de correo](docs/EMAIL_CONFIGURATION.md). Nunca use la contraseña normal del buzón ni incluya una contraseña de aplicación en código, documentación, base de datos, logs o Git.
- `REPORT_XLSX_MAX_ROWS=5000`
- `REPORT_PDF_MAX_ROWS=1000`
- `REPORT_DEFAULT_MAX_DAYS=90`
- `REPORT_LARGE_EXPORT_ALERT_THRESHOLD=1000`

## Limitaciones y riesgos pendientes

Faltan KMS/Key Vault, rotación operativa de llaves, SIEM/log externo inmutable, pruebas PostgreSQL y de concurrencia, pentest, QA visual completo, VPN/allowlist, backups cifrados probados y revisión formal de cumplimiento. El restablecimiento de contraseña por correo no se habilitó: antes requiere diseño anti-enumeración y recuperación organizacional controlada.

### Requerimiento empresarial adicional pendiente

Existe un requerimiento relacionado con un código adicional de seguridad asociado a las tarjetas. **No fue implementado**: no existen modelo, migración, formulario, persistencia, demo, revelado ni copia. Requiere concepto formal de seguridad/cumplimiento y decisión arquitectónica antes de cualquier desarrollo.
