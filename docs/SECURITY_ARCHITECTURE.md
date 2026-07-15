# Arquitectura de seguridad de A&S Vault

## Activos y amenazas

Los activos son PAN, vencimiento, identidad del titular, asociaciones de cliente, credenciales, factores OTP, sesiones, llaves y trazabilidad. Se consideran abuso interno, cuentas comprometidas, IDOR, fuerza bruta, robo/fijación de sesión, exposición frontend/logs, manipulación de auditoría, compromiso de base de datos y pérdida de llaves.

CVV/CVC, PIN, banda magnética y fotografías completas están prohibidos. Existe un requerimiento empresarial adicional relacionado con un código de seguridad de tarjeta, pero permanece pendiente de concepto formal y decisión arquitectónica; no se creó ningún campo ni flujo.

## Límites y roles

Navegador, aplicación, base de datos, correo y futuro KMS son límites separados. El backend es la autoridad. El Administrador gestiona identidad, sesiones, dispositivos, alertas y auditoría sin rutas de tarjetas. Líder y Analista mantienen sus permisos operativos definidos; un perfil nuevo queda inactivo y sin rol.

## Cifrado y llaves

PAN y vencimiento usan Fernet. Un HMAC con secreto independiente identifica duplicados. La session key Django solo se conserva cifrada para revocación; auditorías, grants y relaciones usan SHA-256. Producción requiere KMS/HSM, identidad administrada, versionado y recifrado auditado.

## MFA, sesiones y dispositivos

`django-otp` implementa TOTP estándar. La contraseña genera únicamente una preautenticación de cinco minutos; la sesión Django completa nace después de TOTP o recuperación. Los secretos TOTP no se registran, no se envían y se excluyen del Admin.

Los códigos de recuperación usan el hasher de contraseñas de Django y se consumen una vez. Las sesiones se revocan tanto en el modelo de seguridad como en `django_session`. Solo una puede permanecer activa. La actividad se actualiza con throttling y expira tras diez minutos.

El dispositivo se identifica prudentemente mediante HMAC del User-Agent normalizado, sin fingerprinting invasivo. Ser reconocido no elimina controles. Bloquearlo revoca sus sesiones; solo el Administrador puede desbloquearlo mediante motivo y reautenticación.

## Reautenticación

Cada autorización contiene usuario, hash de sesión, propósito, validación y vencimiento. No existe un booleano global. Cerrar/revocar/expirar la sesión, cambiar contraseña o reiniciar MFA invalida grants y revelados.

## Alertas y auditoría

Las alertas contienen tipo, severidad, estado, actores, IP, dispositivo, descripción y metadatos seguros. No contienen PAN, vencimiento, OTP ni códigos. La auditoría encadena datos canónicos con SHA-256 y una secuencia serializada; detecta manipulación pero no reemplaza un registro externo inmutable.

El Centro de Control agrega politicas, festivos, excepciones, transiciones de alertas, historial de notificaciones y ejecuciones programadas. Estas tablas referencian usuarios con borrado protegido cuando la trazabilidad administrativa lo exige. Alertas, transiciones y notificaciones no se pueden borrar desde las interfaces provistas.

`evaluate_access_policy()` es la unica autoridad horaria. Devuelve permiso, pertenencia al horario, motivo, politica, severidad, necesidad de reautenticacion/alerta/bloqueo y excepcion aplicada. Vistas sensibles consumen esa decision sin duplicar calendarios.

Microsoft Graph se autentica mediante MSAL y credenciales de aplicacion tomadas solo del entorno. No se persisten client secrets, access tokens ni contenido del mensaje. La bitacora de correo conserva destinatario enmascarado, hash, resultado, intentos, backend e identificador externo.

## Riesgos residuales

Capturas o fotografías del dato revelado, portapapeles del sistema, administradores de infraestructura, llaves en proceso, falta de KMS/SIEM, ausencia de pruebas PostgreSQL/concurrencia y falta de pentest/QA visual. A&S Vault no debe recibir datos reales hasta cerrar estos riesgos y validar infraestructura y cumplimiento.
