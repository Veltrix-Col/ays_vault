# Perfil global Zoho para Cotización – Colectivos

## Selector único

Todas las aplicaciones funcionales del Banco de Herramientas heredan el mismo
perfil global:

```env
ZOHO_ACTIVE_PROFILE=sandbox
```

Solo se aceptan `sandbox` y `production`. El valor se normaliza a minúsculas,
sin espacios exteriores. Un valor vacío o desconocido impide iniciar Django.
El valor predeterminado seguro es `sandbox`.

No existen overrides por aplicación. Cotización – Colectivos lee únicamente
`settings.ZOHO_ACTIVE_PROFILE`, nunca un parámetro, formulario, cookie o sesión.
Un selector legado específico de Colectivos presente en un `.env` antiguo se
ignora y debe eliminarse manualmente para evitar confusión operativa.

## Sandbox

```env
ZOHO_ACTIVE_PROFILE=sandbox
ZOHO_SANDBOX_ENABLED=true
ZOHO_PRODUCTION_ENABLED=true
ZOHO_PUBLIC_SETUP_ENABLED=false
```

La interfaz muestra `Sandbox · Solo lectura`. La fachada verifica que
Organization API reporte `type=sandbox` antes de ejecutar búsquedas o fichas.

## Producción

```env
ZOHO_ACTIVE_PROFILE=production
ZOHO_SANDBOX_ENABLED=true
ZOHO_PRODUCTION_ENABLED=true
ZOHO_PUBLIC_SETUP_ENABLED=false
```

El perfil Production debe tener credenciales, refresh token, environment, API
base URL y resource path completos, propios y coherentes. La interfaz muestra
`Producción · Solo lectura` con una variante visual de advertencia. Organization
API debe reportar `type=production`; una inconsistencia bloquea la operación sin
intentar Sandbox.

Ambos perfiles pueden estar habilitados porque la integración mantiene
separados credenciales, tokens, clientes, caché y resource paths. La aplicación
funcional utiliza solamente el perfil activo y nunca hace fallback.

## Aplicación web y comandos

La aplicación web hereda siempre `ZOHO_ACTIVE_PROFILE`. Los comandos
administrativos que reciben `--profile` conservan esa selección explícita:

```powershell
python manage.py zoho_backend_info
python manage.py zoho_backend_info --profile sandbox
python manage.py zoho_check_connection --profile sandbox
python manage.py zoho_check_connection --profile production
```

Sin `--profile`, `zoho_backend_info` informa y valida el perfil global activo.
Con `--profile`, el operador ejecuta conscientemente el diagnóstico indicado sin
cambiar `.env`. Los comandos históricos de descubrimiento y profiling de
Colectivos permanecen cerrados a Sandbox por diseño.

## Solo lectura y privacidad

En ambos ambientes se conservan: máximo 20 resultados, documentos enmascarados,
tokens firmados, anti-IDOR, ausencia de persistencia local y acciones futuras
deshabilitadas. No existen operaciones de creación, actualización, eliminación,
carga o sincronización hacia Zoho.

Los logs funcionales incluyen aplicación, perfil, operación, duración, cantidad
de resultados, categoría de error, usuario interno y correlación. No incluyen
documentos, nombres, búsquedas completas, IDs Zoho completos, tokens, secretos,
headers ni cuerpos.

## Cambio seguro y QA manual

1. Establecer `ZOHO_ACTIVE_PROFILE=sandbox` o `production`.
2. Cerrar cualquier servidor anterior.
3. Reiniciar Django.
4. Ejecutar `zoho_check_connection --profile <perfil>` con autorización.
5. Abrir `/cotizacion-colectivos/`.
6. Confirmar el badge y que los datos pertenecen al ambiente esperado.
7. Probar búsqueda, ficha y relaciones con datos autorizados.
8. Confirmar documentos enmascarados y ausencia de escritura.

No deben coexistir servidores con perfiles diferentes en el mismo puerto.

## Errores frecuentes

- Valor global vacío o inválido: Django falla al arrancar.
- Perfil deshabilitado o incompleto: no se inicia la consulta funcional.
- Entorno reportado distinto: la consulta se bloquea sin fallback.
- Cambio no visible: cerrar procesos Django antiguos y reiniciar.

## Rollback

```env
ZOHO_ACTIVE_PROFILE=sandbox
```

Reiniciar Django y comprobar `Sandbox · Solo lectura`. No requiere modificar
código ni eliminar las credenciales productivas.

