# Arquitectura de seguridad de A&S Vault

## Activos y amenazas

Los activos son PAN, vencimiento, identidad del titular, asociaciones de cliente, credenciales, factores OTP, sesiones, llaves y trazabilidad. Se consideran abuso interno, cuentas comprometidas, IDOR, fuerza bruta, robo/fijación de sesión, exposición frontend/logs, manipulación de auditoría, compromiso de base de datos y pérdida de llaves.

CVV/CVC, PIN, banda magnética y fotografías completas están prohibidos. Existe un requerimiento empresarial adicional relacionado con un código de seguridad de tarjeta, pero permanece pendiente de concepto formal y decisión arquitectónica; no se creó ningún campo ni flujo.

## Límites y roles

Navegador, aplicación, base de datos, correo y futuro KMS son límites separados. El backend es la autoridad. El Administrador gestiona identidad, sesiones, dispositivos, alertas, auditoría e informes sin rutas de Bóveda ni valores protegidos. Líder y Analista solo acceden a Bóveda; el Líder administra tarjetas y el Analista consulta activas. Un perfil nuevo queda inactivo y sin rol.

## Cifrado y llaves

Empresa, PAN y vencimiento usan Fernet. Empresa no tiene índice en texto claro y no participa en búsquedas. Un HMAC con secreto independiente identifica PAN duplicados. La session key Django solo se conserva cifrada para revocación; auditorías, grants y relaciones usan SHA-256. Producción requiere KMS/HSM, identidad administrada, versionado y recifrado auditado.

## MFA, sesiones y dispositivos

`django-otp` implementa TOTP estándar. La contraseña genera únicamente una preautenticación de cinco minutos; la sesión Django completa nace después de TOTP o recuperación. Los secretos TOTP no se registran, no se envían y se excluyen del Admin.

Los códigos de recuperación usan el hasher de contraseñas de Django y se consumen una vez. Las sesiones se revocan tanto en el modelo de seguridad como en `django_session`. Solo una puede permanecer activa. La actividad se actualiza con throttling y expira tras diez minutos.

El dispositivo se identifica prudentemente mediante HMAC del User-Agent normalizado, sin fingerprinting invasivo. Ser reconocido no elimina controles. Bloquearlo revoca sus sesiones; solo el Administrador puede desbloquearlo mediante motivo y reautenticación.

## Reautenticación

La operación protegida conserva primero una intención aleatoria de cinco minutos en sesión, sin valores protegidos. Contraseña y TOTP validan la identidad y crean `SensitiveOperationWindow`, ligada al usuario/hash de sesión y con expiración fija de 15 minutos; este modelo no contiene motivo ni referencia. Después se exige una justificación nueva y se crea `ProtectedOperationContext`, ligado a una sola tarjeta, a la ventana y a la sesión. Empresa, PAN y vencimiento comparten ese contexto para revelar o copiar, pero otra tarjeta o una nueva operación requieren un contexto nuevo. Cada evento conserva su contexto, campo y acción, y la copia usa un token de un solo uso con 20 segundos de vigencia. Cerrar/revocar/expirar sesión, cambiar contraseña, bloquear dispositivo o reiniciar MFA invalida ambas autorizaciones.

## Alertas y auditoría

Las alertas contienen tipo, severidad, estado, actores, IP, dispositivo, descripción y metadatos seguros. No contienen PAN, vencimiento, OTP ni códigos. La auditoría encadena datos canónicos con SHA-256 y una secuencia serializada; detecta manipulación pero no reemplaza un registro externo inmutable.

El Centro de Control agrega politicas, festivos, excepciones, transiciones de alertas, historial de notificaciones y ejecuciones programadas. Estas tablas referencian usuarios con borrado protegido cuando la trazabilidad administrativa lo exige. Alertas, transiciones y notificaciones no se pueden borrar desde las interfaces provistas.

`evaluate_access_policy()` es la unica autoridad horaria. Devuelve permiso, pertenencia al horario, motivo, politica, severidad, necesidad de reautenticacion/alerta/bloqueo y excepcion aplicada. Vistas sensibles consumen esa decision sin duplicar calendarios.

El servicio de correo admite consola, SMTP de Microsoft 365 y Microsoft Graph mediante una interfaz común. SMTP usa TLS y una contraseña de aplicación tomada solo del entorno; Graph usa MSAL y credenciales de aplicación. No se persisten contraseñas, client secrets, access tokens ni contenido del mensaje. La bitácora conserva destinatario enmascarado, hash, resultado, intentos, backend e identificador externo.

## Informes y limites de datos

La autorización de informes exige Administrador activo antes de consultar datos. `ReportExport` conserva tipo, formato, actor, filtros seguros, cantidad, resultado, duración, IP, dispositivo y nombre saneado, pero nunca el archivo ni los registros exportados.

Los generadores no llaman `get_company()`, `get_pan()` ni `get_expiry()`. Los motivos se truncan y redactan ante patrones de PAN o vencimiento. Excel neutraliza valores que comienzan con `=`, `+`, `-` o `@`. PDF se crea en memoria, y ambas respuestas llevan `no-store` y `nosniff`. Los límites centrales evitan exportaciones accidentales de alto volumen y las generaciones inusuales crean alerta.

## Riesgos residuales

Capturas o fotografías del dato revelado, portapapeles del sistema, administradores de infraestructura, llaves en proceso, falta de KMS/SIEM, ausencia de pruebas PostgreSQL/concurrencia y falta de pentest/QA visual. A&S Vault no debe recibir datos reales hasta cerrar estos riesgos y validar infraestructura y cumplimiento.
