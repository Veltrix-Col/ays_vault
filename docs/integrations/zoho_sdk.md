# Librería interna Zoho CRM V8

Validación técnica: 30 de julio de 2026.

## Contrato y solo lectura

La integración expone una fachada propia. Los consumidores no reciben
credenciales, URLs, TokenStore, objetos HTTP ni clases del SDK:

```python
from integrations.zoho import get_zoho

zoho = get_zoho()
production = get_zoho(profile="production")
sandbox = get_zoho(profile="sandbox")

organization = zoho.organization.get()
modules = zoho.metadata.list_modules()
fields = zoho.metadata.list_fields("Polizas")
page = zoho.records.list(module="Polizas", fields=["id", "Name"], limit=20)
```

La fachada no ofrece `create`, `update`, `upsert`, `delete`, `save`, `write`,
`upload`, `attach`, Bulk Write, Composite Write ni Functions. COQL exige
`SELECT`, limita tamaño y filas, y rechaza comentarios, múltiples sentencias,
DDL y escritura. Los scopes admitidos terminan en `.READ`.

El SDK oficial permanece fijado en `zohocrmsdk8-0==6.0.0`. `sdk` es el backend
predeterminado y `rest` es rollback/fallback controlado. Cambiar el backend no
altera el contrato del consumidor ni el perfil.

## Arquitectura multiperfil

Los únicos perfiles válidos son:

- `production`;
- `sandbox`;
- `qa`;
- `demo`;
- `future`.

`ZOHO_ACTIVE_PROFILE` decide qué perfil usan `get_zoho()` y todas las
aplicaciones funcionales. Su valor global solo puede ser `sandbox` o
`production` y el predeterminado seguro es `sandbox`; no existen overrides por
aplicación. Los comandos y el código administrativo interno autorizado pueden
seleccionar un perfil explícitamente con
`get_zoho(profile="sandbox")`. Un nombre inválido, un perfil deshabilitado o
una configuración incompleta producen `ZohoConfigurationError`; nunca se
cambia automáticamente a Producción.

La caché de proceso usa `(profile, backend)` como clave. Cada perfil conserva
su propia fachada, configuración, cliente REST, TokenStore en memoria, access
token y resource path. El reset de pruebas puede limpiar un perfil sin afectar
los demás.

### SDK y aislamiento

Mapeo cerrado:

- `production` → `USDataCenter.PRODUCTION()` →
  `https://www.zohoapis.com`;
- `sandbox` → `USDataCenter.SANDBOX()` →
  `https://sandbox.zohoapis.com`.

El Accounts Data Center permitido es `https://accounts.zoho.com`. No se
admiten otros Data Centers sin una revisión explícita.

El inicializador del SDK oficial es global. Por eso, cada operación SDK toma
un bloqueo de proceso durante la inicialización y toda la consulta. Al cambiar
de perfil se instala el entorno y TokenStore correctos antes de ejecutar la
operación. Esto impide que otro hilo cambie el singleton a mitad de una
consulta. El coste es que las llamadas SDK de perfiles distintos se
serializan dentro del mismo worker.

La Organization API valida el entorno cuando Zoho lo informa. Una respuesta
`sandbox` para Producción, o `production` para Sandbox, bloquea el comando y
no permite continuar con exportaciones ni diagnósticos. No hay fallback de
entorno.

## Configuración

Variables globales no identitarias:

```env
ZOHO_ACTIVE_PROFILE=sandbox
ZOHO_BACKEND=sdk
ZOHO_OAUTH_SCOPES=ZohoCRM.org.READ,ZohoCRM.settings.modules.READ,ZohoCRM.settings.fields.READ,ZohoCRM.modules.READ,ZohoCRM.coql.READ
ZOHO_REQUEST_TIMEOUT_SECONDS=15
ZOHO_MAX_RETRIES=2
ZOHO_SDK_LOG_LEVEL=INFO
ZOHO_PUBLIC_SETUP_ENABLED=false
```

Producción:

```env
ZOHO_PRODUCTION_ENABLED=true
ZOHO_PRODUCTION_CLIENT_ID=<secreto>
ZOHO_PRODUCTION_CLIENT_SECRET=<secreto>
ZOHO_PRODUCTION_REFRESH_TOKEN=<secreto>
ZOHO_PRODUCTION_EXPECTED_ORG_ID=
ZOHO_PRODUCTION_ENVIRONMENT=production
ZOHO_PRODUCTION_ACCOUNTS_BASE_URL=https://accounts.zoho.com
ZOHO_PRODUCTION_API_BASE_URL=https://www.zohoapis.com
ZOHO_PRODUCTION_SDK_RESOURCE_PATH=runtime/zoho_sdk/production
```

Sandbox:

```env
ZOHO_SANDBOX_ENABLED=false
ZOHO_SANDBOX_CLIENT_ID=
ZOHO_SANDBOX_CLIENT_SECRET=
ZOHO_SANDBOX_REFRESH_TOKEN=
ZOHO_SANDBOX_EXPECTED_ORG_ID=
ZOHO_SANDBOX_ENVIRONMENT=sandbox
ZOHO_SANDBOX_ACCOUNTS_BASE_URL=https://accounts.zoho.com
ZOHO_SANDBOX_API_BASE_URL=https://sandbox.zohoapis.com
ZOHO_SANDBOX_SDK_RESOURCE_PATH=runtime/zoho_sdk/sandbox
```

Reservados y cerrados por defecto:

```env
ZOHO_QA_ENABLED=false
ZOHO_DEMO_ENABLED=false
ZOHO_FUTURE_ENABLED=false
```

No contienen credenciales ficticias ni heredan configuración.

### Compatibilidad heredada

Solo `production` puede usar temporalmente `ZOHO_CLIENT_ID`,
`ZOHO_CLIENT_SECRET`, `ZOHO_REFRESH_TOKEN`, `ZOHO_ACCOUNTS_BASE_URL`,
`ZOHO_API_BASE_URL` y `ZOHO_SDK_RESOURCE_PATH` si no existe ninguna
credencial `ZOHO_PRODUCTION_*`. El sistema emite un warning saneado sin
valores. Sandbox, QA, Demo y Future nunca heredan estas variables. Esta
compatibilidad debe retirarse después de migrar los secretos.

## Tokens y resource paths

El TokenStore personalizado:

- no usa `DBStore` ni `FileStore`;
- no escribe base de datos, `.env`, sesión, cookie o archivo permanente;
- mantiene el access token solo en memoria y separado por perfil;
- nunca reemplaza el refresh token configurado;
- rechaza un mismo refresh token configurado en dos perfiles.

Los resource paths predeterminados son:

```text
runtime/zoho_sdk/production
runtime/zoho_sdk/sandbox
runtime/zoho_sdk/qa
runtime/zoho_sdk/demo
runtime/zoho_sdk/future
```

Se crean únicamente al inicializar el perfil. Son caché regenerable, están
ignorados por Git y no contienen tokens ni registros. Dos perfiles no pueden
resolver a la misma ruta.

## OAuth por perfil

El flujo conserva POST, CSRF, sesión, expiración y uso único. El `state`
incluye el perfil y un digest que lo vincula al valor aleatorio, la sesión y
el usuario cuando existe. El callback recupera el perfil solo del state
validado; no confía en query strings ni campos libres.

La entrega efímera identifica perfil y entorno y nunca muestra dos tokens al
mismo tiempo. No escribe `.env`.

### Validacion concluyente del entorno OAuth

El `api_domain` de la respuesta de tokens se conserva como diagnostico y se
valida mediante una allowlist cerrada (`www.zohoapis.com` y
`sandbox.zohoapis.com`). No se usa por si solo para inferir el entorno. La
guia oficial especifica de Sandbox indica que el entorno real del token se
identifica consultando `GET https://www.zohoapis.com/crm/v8/org`, incluso para
tokens Sandbox, y leyendo el campo `type` (`production`, `sandbox` o
`developer`). Esto resuelve la aparente contradiccion con la guia general de
tokens, que describe un `api_domain` dependiente del entorno.

Durante el callback, access token y refresh token permanecen como candidatos
locales. El sistema consulta la Organization API con el access token candidato
sin publicarlo en el TokenStore. `production` exige `type=production` y
`sandbox` exige `type=sandbox`. Solo tras esa coincidencia se publican ambos
tokens atomica y exclusivamente en el perfil solicitado. Las operaciones
posteriores usan `https://www.zohoapis.com` en Produccion y
`https://sandbox.zohoapis.com` en Sandbox.

El SDK V8 representa `Organization.type` mediante `Choice`; su texto se
obtiene con `get_value()`. La normalizacion canonica admite el `Choice`
oficial, strings REST, objetos con `get_value()` y objetos con atributo
`value`, y devuelve unicamente `production`, `sandbox`, `developer` o
`bigin`. Los valores ausentes o desconocidos fallan cerradamente. Esta misma
normalizacion se aplica al OAuth, al backend SDK, al backend REST y a la
fachada usada por `zoho_check_connection`. Los metadatos `generated_type`
usan la extraccion textual segura para no registrar representaciones como
`<Choice object at ...>`.

Opcionalmente, `ZOHO_PRODUCTION_EXPECTED_ORG_ID` y
`ZOHO_SANDBOX_EXPECTED_ORG_ID` fijan la identidad exacta de la organizacion.
Si estan vacias, se valida solo `type`; si estan configuradas, el ID debe
coincidir exactamente. Los IDs nunca se registran en logs ni se generan de
forma automatica.

Referencias oficiales:

- https://help.zoho.com/portal/en/community/topic/kaizen-120-a-guide-to-api-calls-in-zoho-crm-sandboxes
- https://www.zoho.com/crm/developer/docs/api/v8/get-org-data.html
- https://www.zoho.com/crm/developer/docs/api/v8/auth-request.html
- https://www.zoho.com/crm/developer/docs/api/v8/access-refresh.html

Procedimiento local para obtener el token de Sandbox:

1. Configurar `ZOHO_SANDBOX_CLIENT_ID` y
   `ZOHO_SANDBOX_CLIENT_SECRET`.
2. Usar `ZOHO_SANDBOX_ENABLED=true`, `DEBUG=true` y, solo durante la
   autorización local, `ZOHO_PUBLIC_SETUP_ENABLED=true`.
3. Abrir el estado Zoho e iniciar OAuth para `sandbox`.
4. Autorizar la organización `Pruebas AYS`.
5. La aplicacion consulta `www.zohoapis.com/crm/v8/org` y exige
   `type=sandbox` antes de aceptar los tokens.
6. Confirmar `Perfil: sandbox` y `Entorno: sandbox`.
7. Copiar una sola vez el valor a `ZOHO_SANDBOX_REFRESH_TOKEN`.
8. Reiniciar el proceso y restaurar `ZOHO_PUBLIC_SETUP_ENABLED=false`.

El refresh token productivo no se reemplaza. Aunque se reutilice el mismo
cliente OAuth, los refresh tokens de Producción y Sandbox deben ser distintos.

## Comandos

```powershell
python manage.py zoho_backend_info --profile production
python manage.py zoho_backend_info --profile sandbox
python manage.py zoho_check_connection --profile sandbox
python manage.py zoho_export_schema --profile sandbox --module Polizas
python manage.py zoho_diagnose_modules --profile sandbox --module Polizas
```

Los comandos muestran perfil, entorno, backend, modo solo lectura,
organización y resource path saneado. Nunca muestran secretos. Los comandos de
conexión, exportación y diagnóstico realizan lecturas reales cuando se
ejecutan manualmente; las pruebas siempre los simulan.

`zoho_export_schema` genera solo metadatos, nunca registros. El diagnóstico
consulta Organization, Modules y Fields, clasifica errores y su JSON opcional
se limita a datos técnicos seguros. `artifacts/zoho/` permanece ignorado.

## Dokploy

### Producción

```env
ZOHO_ACTIVE_PROFILE=production
ZOHO_BACKEND=sdk
ZOHO_PUBLIC_SETUP_ENABLED=false
ZOHO_PRODUCTION_ENABLED=true
ZOHO_PRODUCTION_CLIENT_ID=<secreto>
ZOHO_PRODUCTION_CLIENT_SECRET=<secreto>
ZOHO_PRODUCTION_REFRESH_TOKEN=<secreto>
ZOHO_PRODUCTION_EXPECTED_ORG_ID=<opcional>
ZOHO_PRODUCTION_ENVIRONMENT=production
ZOHO_PRODUCTION_ACCOUNTS_BASE_URL=https://accounts.zoho.com
ZOHO_PRODUCTION_API_BASE_URL=https://www.zohoapis.com
ZOHO_PRODUCTION_SDK_RESOURCE_PATH=/app/runtime/zoho_sdk/production
ZOHO_SANDBOX_ENABLED=false
```

### Staging contra Sandbox

```env
ZOHO_ACTIVE_PROFILE=sandbox
ZOHO_BACKEND=sdk
ZOHO_PUBLIC_SETUP_ENABLED=false
ZOHO_PRODUCTION_ENABLED=false
ZOHO_SANDBOX_ENABLED=true
ZOHO_SANDBOX_CLIENT_ID=<secreto aprobado>
ZOHO_SANDBOX_CLIENT_SECRET=<secreto aprobado>
ZOHO_SANDBOX_REFRESH_TOKEN=<refresh token exclusivo de Sandbox>
ZOHO_SANDBOX_EXPECTED_ORG_ID=<opcional>
ZOHO_SANDBOX_ENVIRONMENT=sandbox
ZOHO_SANDBOX_ACCOUNTS_BASE_URL=https://accounts.zoho.com
ZOHO_SANDBOX_API_BASE_URL=https://sandbox.zohoapis.com
ZOHO_SANDBOX_SDK_RESOURCE_PATH=/app/runtime/zoho_sdk/sandbox
```

Staging no debe recibir refresh token productivo ni variables heredadas
productivas. El mismo Client ID/Secret puede reutilizarse únicamente con
aprobación expresa para el cliente OAuth `A&S Banco de Herramientas`; ello no
autoriza compartir refresh tokens, API URLs, entorno SDK o resource path.

Rollback: cambiar `ZOHO_BACKEND=rest` y reiniciar workers. El backend REST
conserva el perfil; no hace fallback a otro entorno. Para deshabilitar, usar
`ZOHO_<PROFILE>_ENABLED=false`.

La rotación o revocación se hace en Zoho y después en el secreto Dokploy del
perfil correspondiente. Deben revocarse tokens obsoletos de manera controlada
porque Zoho puede limitar el número de refresh tokens activos.

## QA y riesgos pendientes

QA manual, usando exclusivamente secretos autorizados:

1. ejecutar `zoho_backend_info --profile sandbox`;
2. ejecutar `zoho_check_connection --profile sandbox`;
3. confirmar organización `Pruebas AYS` y entorno Sandbox;
4. exportar `Polizas` y revisar solo metadatos;
5. repetir información/conexión en Producción de forma separada;
6. revisar que ningún log muestre credenciales.

Riesgos y decisiones:

- el singleton oficial serializa llamadas SDK multiperfil por worker;
- cada worker mantiene sus propios access tokens en memoria;
- los rate limits pertenecen a la organización;
- el resource path es caché, no almacenamiento durable;
- una futura capacidad de escritura requiere otra revisión de seguridad,
  scopes y arquitectura;
- la compatibilidad heredada de Producción debe retirarse;
- QA, Demo y Future permanecen deshabilitados hasta configuración aprobada.
