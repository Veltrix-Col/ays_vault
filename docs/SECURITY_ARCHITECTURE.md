# Arquitectura de seguridad de A&S Vault

## Alcance y activos

Los activos principales son PAN, vencimiento, identidad del titular, asociación con cliente, credenciales, sesiones, llaves criptográficas y trazabilidad. CVV/CVC, PIN, banda magnética y fotografías completas están fuera de alcance y no deben almacenarse.

## Amenazas y límites de confianza

Se consideran abuso interno, cuentas comprometidas, IDOR, fuerza bruta, copia no trazada, exposición en HTML/JSON/logs, manipulación de auditoría, robo de sesión, inyección, compromiso de base de datos y pérdida de llaves. Navegador, aplicación, base de datos, correo y futuro KMS son límites separados. El backend es la autoridad de permisos; ocultar botones no concede seguridad.

La aplicación no puede impedir de forma absoluta capturas de pantalla, fotografías, herramientas del sistema operativo ni todas las variantes de copiar/pegar. Controla y audita sus propios botones y minimiza el tiempo visible.

## Roles

- Administrador: usuarios, configuración, alertas, auditoría e integridad; sin rutas de tarjetas, revelado o copia.
- Líder de cartera: alta, edición, desactivación lógica, consulta, revelado y copia.
- Analista: consulta de tarjetas activas, revelado y copia.
- Sin rol/inactivo: acceso denegado por defecto.

## Cifrado y llaves

PAN y vencimiento usan Fernet mediante una capa dedicada. Un HMAC-SHA256 con secreto independiente permite detectar duplicados. Los últimos cuatro dígitos quedan visibles. Las llaves llegan por entorno solo en desarrollo; producción debe usar Azure Key Vault, AWS KMS o equivalente, identidad administrada, versionado y rotación auditada. Nunca deben aparecer en código, logs o backups sin cifrar.

## Revelado y copia

El revelado exige usuario activo, rol, tarjeta activa, motivo, campo explícito y contraseña. Solo devuelve ese campo con `Cache-Control: no-store`. La copia requiere un token aleatorio almacenado como hash, ligado a usuario, sesión, tarjeta y campo, expira en 25 segundos y se consume una vez. El valor copiado no se audita.

## Auditoría e integridad

Los eventos guardan actor, rol, acción, objeto interno, tiempo, IP, User-Agent, sesión, ruta, método, resultado, riesgo, motivo y metadatos no sensibles. Una fila singleton serializa la secuencia; cada hash cubre el hash anterior y datos canónicos. `verify_audit_chain` valida continuidad y contenido. Esto detecta manipulación, pero no reemplaza almacenamiento externo append-only.

## Sesiones, HTTP y fuerza bruta

La sesión expira tras 10 minutos de inactividad y al cerrar navegador. Cookies HttpOnly/SameSite, CSRF, CSP, Permissions Policy, anti-clickjacking y no-cache protegen el canal web. Secure cookies, redirección HTTPS y HSTS se activan cuando `DEBUG=False`. Axes limita intentos por usuario e IP. Un proxy confiable debe sobrescribir la IP remota; la aplicación no confía directamente en `X-Forwarded-For`.

## Alertas y riesgo

El MVP clasifica operaciones fuera de horario y resultados fallidos como riesgo alto, crea alertas persistentes y puede enviar correo sin datos completos. Estados previstos: Nueva, Revisada, Justificada, Escalada y Cerrada. Aún faltan interfaz de gestión, comentario obligatorio, umbrales configurables y detección de IP/dispositivo nuevos.

## MFA y riesgos residuales

MFA todavía no está implementado. Debe integrarse con `django-otp` o equivalente mantenido, códigos de respaldo, recuperación controlada y reautenticación reforzada. Hasta entonces no se permite uso con datos reales.

Riesgos residuales adicionales: llaves en proceso/entorno, portapapeles del sistema, endpoint de correo, superusuarios de infraestructura, SQLite local, falta de sesión única, ausencia de calendario de festivos y falta de SIEM inmutable.

## Infraestructura requerida

PostgreSQL administrado, TLS extremo a extremo, KMS/HSM, secretos administrados, VPN o allowlist, mínimos privilegios, WAF/reverse proxy, logs inmutables/SIEM, backups cifrados y probados, monitoreo/alertamiento, EDR, escaneo de dependencias, pruebas de penetración, revisión PCI/legal y procedimientos de altas/bajas, incidentes, rotación y recuperación.
