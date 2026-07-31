# Integración Zoho CRM API V8

La arquitectura vigente, la configuración multiperfil, el SDK oficial, OAuth,
los comandos y el despliegue están documentados en
[zoho_sdk.md](zoho_sdk.md).

## Alcance

La aplicación `integrations` encapsula Zoho CRM sin acoplarla con Vault, SOAT
o Portal. La fase actual es exclusivamente de lectura: organización, módulos,
campos, registros y COQL `SELECT`. No implementa creación, actualización,
upsert, eliminación, adjuntos, tareas ni ninguna otra escritura.

## Reglas operativas esenciales

- Use `ZOHO_PRODUCTION_*` para Producción y `ZOHO_SANDBOX_*` para Sandbox.
- `ZOHO_ACTIVE_PROFILE` selecciona el perfil predeterminado.
- Las variables Zoho sin prefijo son compatibilidad temporal exclusiva de
  Producción.
- Sandbox nunca hereda credenciales, tokens, URLs o caché de Producción.
- Producción usa el entorno SDK oficial Production y
  `https://www.zohoapis.com`.
- Sandbox usa el entorno SDK oficial Sandbox y
  `https://sandbox.zohoapis.com`.
- El Data Center soportado es `.com`.
- Todos los scopes terminan en `.READ`; no se solicita `ALL`.
- `ZOHO_PUBLIC_SETUP_ENABLED` debe permanecer en `false` fuera de una
  configuración local temporal.

## OAuth local

El flujo conserva POST, CSRF, state aleatorio, sesión, expiración y uso único.
El perfil queda vinculado al state y el callback no confía en parámetros
libres.

Bajo `DEBUG=true` y modo público local, el callback puede presentar una copia
de una sola lectura del refresh token a la misma sesión, identificada por
perfil y entorno. Debe copiarse al secreto
`ZOHO_<PROFILE>_REFRESH_TOKEN`. La aplicación nunca escribe `.env`, base de
datos o archivos de tokens.

## Comandos

```powershell
python manage.py zoho_backend_info --profile production
python manage.py zoho_check_connection --profile sandbox
python manage.py zoho_export_schema --profile sandbox --module Polizas
python manage.py zoho_diagnose_modules --profile sandbox --module Polizas
```

Las pruebas automatizadas usan mocks y no llaman a Zoho. Los comandos
anteriores sí realizan lecturas reales cuando un operador los ejecuta
manualmente con secretos autorizados.

## Seguridad

Nunca suba Client ID, Client Secret, refresh token, access token,
authorization code ni `.env` a Git. El access token vive solo en memoria y
separado por perfil. Los resource paths son caché regenerable separada y están
ignorados por Git.

Una futura capacidad de escritura requiere otra revisión de seguridad,
scopes, idempotencia, auditoría y rollback. No está autorizada por esta
implementación.
