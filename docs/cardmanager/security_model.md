# Modelo de seguridad de CardManager

## Controles implementados

- Contraseña + TOTP; django-axes limita intentos por usuario/IP.
- Códigos de recuperación hasheados y de un solo uso.
- Sesión segura única/limitada según política, expiración por inactividad y revocación.
- Inventario de dispositivos con estados nuevo, confiable, bloqueado y revocado.
- Reautenticación y contexto específico antes de revelar/copiar.
- Grants temporales, ligados a sesión/usuario/tarjeta y consumibles una sola vez.
- Roles validados en backend, anti-IDOR mediante QuerySets autorizados.
- CSRF en POST; `never_cache`, `no-store`, `nosniff`, `DENY`, CSP y Permissions-Policy.
- Cifrado simétrico de campos y HMAC/fingerprint separado para igualdad.
- Auditoría hash encadenada y detección de manipulación.
- Redacción de logs/correos/reportes y neutralización de fórmulas Excel.

## Cabeceras y cookies

Cookies HTTP-only, SameSite Lax; en no-DEBUG se fuerzan cookies seguras, HTTPS, HSTS anual con subdominios/preload y cabecera de proxy. Las rutas de Vault/control/reportes/seguridad se marcan privadas y no cacheables.

## Límites y riesgos residuales

- Las llaves siguen siendo configuración de entorno: no hay KMS/Key Vault demostrado.
- La cadena hash detecta alteración, pero no equivale por sí sola a un WORM externo.
- No hay evidencia de pentest ni certificación PCI.
- El campo “Código” se revela bajo el mismo flujo protegido; su necesidad y clasificación requieren decisión formal de A&S.
- Backups, SIEM y procedimientos de custodia deben validarse en infraestructura real.
