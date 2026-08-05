# Workspace operativo de póliza

> La sección que describe el constructor multipóliza es histórica. El
> Workspace vigente genera el enlace directamente y no expone constructor,
> bandeja, revisión o aprobación como pasos operativos.

## Objetivo

La ficha de póliza es el centro operativo de Cotización–Colectivos. El analista puede revisar la información confirmada, el grupo, la actividad, las solicitudes y el acceso del cliente sin abandonar la ficha. El módulo continúa siendo de solo lectura frente a Zoho.

## Organización

- **Resumen:** estado, ramo, tomador, aseguradora, vigencias, renovación, pagos, planes y valores disponibles.
- **Grupo asegurado:** indicadores, asegurados, afiliados, beneficiarios y riesgos, con detalle progresivo.
- **Actividad:** línea de tiempo con etiquetas funcionales y fechas.
- **Novedades:** solicitudes y respuestas asociadas exclusivamente a la póliza, entidad y perfil.
- **Cliente:** generar, regenerar, copiar, abrir y revocar el acceso.
- **Herramientas:** actualizar desde Zoho, descargar Excel, ver grupo y abrir el constructor multipóliza.
- **Historial:** trazabilidad consolidada de la operación.

Generar o reutilizar un acceso renderiza nuevamente el workspace. El enlace completo se muestra una sola vez; un acceso vigente reutilizado no vuelve a revelar el token.

## Miniportal

La interfaz externa usa lenguaje de cliente: “Mi póliza”, “mi grupo”, “familia, beneficiarios y coberturas”. Las relaciones técnicas se consolidan antes del template y se muestran como tarjetas. Los formularios, nombres internos de campos y reglas de validación no cambiaron.

## Rendimiento medido

Medición real, cerrada y de solo lectura en Production sobre una póliza representativa autorizada. Valores en milisegundos:

| Etapa | SDK frío | SDK caliente | REST frío | REST caliente |
|---|---:|---:|---:|---:|
| Crear facade | 3 | 3 / 3 | 4 | 2 / 3 |
| Organization API directa del benchmark | 3283 | 558 / 530 | 467 | 471 / 527 |
| Metadata | 1461 | 1548 / 1258 | 992 | 1128 / 1131 |
| Localizar póliza | 914 | 989 / 1099 | 871 | 805 / 1091 |
| Detalle de póliza | 3639 | 2412 / 2529 | 2161 | 1677 / 1914 |
| Grupo actual | 2523 | 2357 / 2445 | 1928 | 1985 / 1938 |

En el desglose real del grupo SDK frío (16 relaciones), los 3640 ms se distribuyeron en: póliza 906 ms, relaciones 875 ms y contactos 1859 ms. En ejecuciones SDK calientes, el total estuvo entre 2407 y 2532 ms. REST estuvo entre 1687 y 2156 ms.

El render local instrumentado en la suite tomó entre 0 y 31 ms para el workspace. El miniportal tomó entre 15 y 31 ms en los casos que incluyeron consulta local, agrupación y template. Estas cifras locales no sustituyen una medición HTTP de Production.

## Cuello de botella y decisión

El cuello de botella demostrado está en las consultas remotas que construyen el grupo, no en DTO, agrupación ni render. En SDK, la lectura directa de `Polizas` devolvió una excepción saneada y la fachada usó su fallback controlado de búsqueda; esto añade latencia al lookup. La consulta de contactos fue la etapa individual más costosa en el pase frío.

La optimización aplicada en esta intervención es reutilizar la preparación cifrada existente al generar el acceso y mantener la operación en la misma ficha. La validación de Organization ya tiene caché breve por perfil/backend y la preparación de póliza ya distingue `hit`, `miss`, `expired`, `invalid`, `refresh_manual` y `active_request_reused`.

Para una iteración posterior, basada en estas mediciones:

1. diagnosticar de forma aislada por qué el SDK falla al obtener la póliza por ID y obliga al fallback;
2. medir el hit real de preparación en tráfico Production antes de cambiar TTL;
3. precalentar únicamente las pólizas abiertas activamente, con límite y sin recorrer carteras completas;
4. mantener REST como comparación/rollback, no cambiar el backend por una sola muestra.

## Privacidad y seguridad

Las métricas registran aplicación, perfil, operación, backend, estado de caché, cantidad y duración. No registran documentos, nombres, correos, tokens, términos, IDs Zoho, cuerpos ni criterios con valores. No se añadió escritura en Zoho ni persistencia de respuestas CRM.

## QA manual

Se validan los anchos 320, 375, 768, 1024 y 1440 px, sin desbordamiento horizontal. El flujo mínimo es: abrir póliza, generar enlace, copiar y abrir. Revocar y regenerar conservan CSRF, permisos y tokens de una sola lectura.
