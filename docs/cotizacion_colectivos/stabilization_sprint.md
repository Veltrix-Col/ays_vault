# Sprint de estabilización de Cotización–Colectivos

## Flujo de producto

El recorrido principal queda limitado a:

1. buscar empresa o individuo;
2. abrir una póliza;
3. consultar su información;
4. generar y copiar un enlace;
5. recibir la respuesta del cliente;
6. consultar la notificación informativa.

Los modelos de solicitud, acceso, preparación y snapshot siguen siendo
infraestructura interna. El recorrido principal no enlaza el constructor
multipóliza, la bandeja, la asignación, la revisión ni la aprobación. Las rutas
históricas se conservan únicamente para compatibilidad y retención de datos.

## Lectura fría de una póliza

La hidratación usa exclusivamente la facade de `integrations.zoho` y operaciones
de lectura cerradas:

- una búsqueda exacta de `Polizas` por el ID firmado;
- páginas de `Riesgos1` limitadas a la póliza;
- lotes COQL de hasta 100 IDs únicos para `Contacts`;
- lotes COQL de hasta 100 IDs únicos para `Riesgos`.

Antes de este sprint, Colectivos intentaba `Polizas.get_by_id`; ante el error del
SDK repetía la misma lectura mediante Search API. La ruta estable ya demostrada
es ahora la operación directa. No se añadió HTTP, backend ni cliente paralelo.

La instrumentación registra duración y conteos agregados para búsqueda de
póliza, páginas de asegurados y lotes de contactos/riesgos. No registra IDs,
criterios con valores, nombres, documentos, tokens ni respuestas.

## Lectura caliente

El snapshot cifrado se guarda en `WorkspacePolizaColectivo` y se copia a la
caché L1. Si L1 no está disponible, L2 reconstruye la caché sin consultar Zoho.
Con un snapshot vigente, detalle, grupo, Excel, enlace y portal externo deben
tener `remote_queries=0`. Solo la acción explícita **Actualizar información
desde Zoho** invalida L1 y reconstruye el snapshot.

La identidad de esa copia local no incluye el modo funcional. Solicitudes,
Invitaciones y Cotización Individual restauran el mismo Workspace de póliza.
El preview y la generación de plantillas, el enlace individual, el formulario
externo y la lectura de su respuesta no inicializan la fachada cuando la copia
es válida.

## Cotización individual contextual

La entrada libre por ramo fue retirada del recorrido. El contrato actual exige
cliente, póliza y afiliado confirmado. La referencia del afiliado es HMAC; el
ramo se deriva de la póliza y determina el esquema. El contexto externo está
firmado, cifrado y tiene expiración. La respuesta y su notificación son locales,
sin escritura ni nueva lectura Zoho.

## Plantillas voluminosas

Las maestras tabulares declaradas como repetibles se generan por lotes de la
capacidad física de cada archivo. Los campos manuales quedan vacíos y no
bloquean la descarga. No se recortan filas ni se reutilizan valores residuales
del maestro: las celdas mapeadas de cada bloque se limpian antes de poblar el
lote actual. Las plantillas no verificadas para repetición fallan antes de
producir una salida parcial.

## Organization API

La validación conserva cierre por ambiente y se almacena por perfil y backend
durante `COLECTIVOS_ORGANIZATION_CACHE_TTL_SECONDS` (300 segundos por defecto).
La configuración actual usa el alias de caché predeterminado de Django. En un
proceso local, LocMem reutiliza la validación; el autoreload, un reinicio o un
worker distinto tiene una caché independiente. Para producción con varios
workers, el despliegue debe proporcionar un backend de caché compartido si se
quiere evitar un cache miss de Organization por proceso. No se relajó la
validación ni se añadió fallback entre perfiles.

## Carga progresiva

No se implementó. La preparación completa es el contrato que garantiza que el
portal y el Excel representen el mismo conjunto íntegro. Dividirla aumentaría
el riesgo de publicar información incompleta y no reduce por sí mismo el total
de consultas remotas.

## Métricas seguras

El comando cerrado `colectivos_benchmark_workspace` informa:

- facade y Organization;
- búsqueda de póliza;
- Riesgos1 y número de páginas;
- Contacts/Riesgos y número de lotes;
- DTO, agrupación, serialización, cifrado y persistencia;
- restauración L2, lectura L1 y Excel local;
- número total de operaciones remotas.

Acepta solo Production, una póliza de la allowlist y la confirmación explícita
`--allow-production-read`. No imprime valores funcionales ni identificadores.

## Pendientes operativos

- ejecutar el benchmark frío y caliente en un proceso sin autoreload;
- confirmar en el despliegue el backend de caché compartido y su TTL;
- retirar físicamente las rutas históricas únicamente después de definir la
  política de retención y compatibilidad de URLs.
- validar en navegador con una póliza autorizada de cada ramo la terminología y
  el prellenado contextual; no se realizó lectura Production en esta iteración.
