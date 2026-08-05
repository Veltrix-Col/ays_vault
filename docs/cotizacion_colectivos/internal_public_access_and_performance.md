# Acceso interno temporal y rendimiento

## Alcance

Cotización – Colectivos dispone temporalmente del modo
`COLECTIVOS_INTERNAL_PUBLIC_ACCESS=true`. El bypass se aplica únicamente al
namespace heredado `cotizacion_colectivos`; SOAT y Vault conservan sus reglas.
CSRF, métodos HTTP, tokens firmados, anti-IDOR, cifrado, hashes, auditoría y las
restricciones de solo lectura de Zoho permanecen activos.

La bandera debe desactivarse cuando la intranet entregue una identidad heredada:

```env
COLECTIVOS_INTERNAL_PUBLIC_ACCESS=false
```

En ese estado vuelve a operar la validación delegada y los permisos ya definidos
en los modelos, sin requerir cambios de esquema.

## Actor técnico

Las escrituras locales del flujo de expedientes que requieren una instancia de
`User` usan el nombre exacto configurado en
`COLECTIVOS_TECHNICAL_ACTOR_USERNAME`. La cuenta se crea únicamente al ejecutar
una operación mutativa, queda activa, sin permisos administrativos y con
contraseña inutilizable. Nunca se selecciona el primer usuario ni se utiliza
`AnonymousUser` como actor de modelo. Una cuenta existente con contraseña,
`is_staff` o `is_superuser` se rechaza.

En modo público no se muestran ni se aceptan campos para asignar usuarios desde
el navegador. El actor técnico es también el propietario de sus notificaciones.

## Diagnóstico de latencia

La ruta crítica anterior ejecutaba `Organization API` al construir cada servicio.
Además, una búsqueda textual podía generar seis llamadas Search API secuenciales:
igualdad y prefijo para tres campos. En individuos, incluso una coincidencia
documental exacta continuaba con el prefijo.

La ruta actual:

1. reutiliza la fachada cacheada por la factory de la integración;
2. valida Organization una vez por perfil/backend y conserva solo el entorno
   normalizado durante 300 segundos;
3. agrupa los tres campos de nombre en un único criterio `OR` cerrado;
4. ejecuta prefijo solo si la consulta exacta no devuelve resultados;
5. ejecuta únicamente campos documentales para entradas numéricas y únicamente
   campos de nombre para entradas textuales;
6. limita campos y resultados como antes.

La metadata de módulos/campos (incluidos sus datos de layout) dispone de helpers
de caché por perfil/backend con TTL de 1800 segundos. No se cachean tokens, OTP,
usuarios, permisos ni datos personales.

## Métricas seguras

Los logs separan: fachada, Organization, metadata, Search API, COQL, mapper,
deduplicación, DTO, template y construcción de respuesta. Solo incluyen categoría,
perfil, duración, conteo y correlación; nunca el término buscado ni identificadores.

La referencia previa observada fue de 7–17 segundos. Las pruebas automatizadas
confirman la reducción estructural de llamadas, pero no sustituyen una medición
real de red. El tiempo posterior debe confirmarse manualmente en el ambiente
objetivo; no se debe declarar un valor real a partir de mocks.

## QA manual

1. Configurar la bandera pública y el actor técnico.
2. Reiniciar Django.
3. Abrir `/cotizacion-colectivos/` en una sesión anónima.
4. Verificar búsquedas exactas y por prefijo de empresa e individuo.
5. Abrir ficha, póliza, grupo, expedientes, bandeja y notificaciones.
6. Verificar que los POST sin CSRF devuelven 403.
7. Revisar los logs de tiempos y confirmar una primera validación Organization
   con `cache=miss`, seguida por `cache=hit` durante cinco minutos.
8. Desactivar la bandera y comprobar que vuelve a exigirse el acceso heredado.

## Concurrencia local y base de datos

SQLite se mantiene únicamente para desarrollo. Su configuración usa un timeout
de 20 segundos y transacciones `IMMEDIATE`, de modo que una segunda escritura
local espere un bloqueo breve en vez de fallar inmediatamente. Estas opciones no
se aplican cuando `DB_ENGINE` selecciona PostgreSQL.

Marcar una notificación como leída es una operación idempotente y no crítica. La
vista realiza como máximo un reintento corto ante `database is locked`; si el
bloqueo continúa, registra solo la categoría técnica saneada y abre de todas
formas la solicitud relacionada. Nunca acepta una URL de destino almacenada por
el cliente.

SQLite sobre una carpeta sincronizada por OneDrive sigue siendo vulnerable a
contención y bloqueos del archivo. El despliegue productivo debe usar PostgreSQL;
el timeout local es una mitigación de desarrollo, no un sustituto de una base de
datos servidor.

La validación de Organization conserva una caché breve por perfil. Cualquier
optimización adicional debe mantener la comprobación de ambiente y nunca hacer
fallback entre Sandbox y Producción.
