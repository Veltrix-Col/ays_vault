# Policy Workspace local

## Objetivo

La póliza abierta es una unidad de trabajo local. Zoho continúa siendo la fuente de verdad y se consulta solamente durante la hidratación inicial, cuando el Workspace vence o cuando un usuario autorizado pulsa **Actualizar información desde Zoho**.

## Flujo

```text
Selección de póliza
  -> validación del token firmado y del perfil
  -> hidratación cerrada de Polizas, Riesgos1, Contacts y Riesgos
  -> DTO completo
  -> agrupación por referencias HMAC
  -> preparación y snapshot cifrados
  -> Workspace persistente local
  -> ficha / grupo / Excel / solicitud / acceso / miniportal (local)
```

La hidratación es una sola operación de negocio, aunque Zoho requiera varias llamadas paginadas para módulos distintos. No se afirma que sea una única petición HTTP. Los contadores `remote_queries`, `records_queries`, `search_queries` y `coql_queries` exponen la cantidad real sin registrar criterios ni identificadores.

## Persistencia y cifrado

`WorkspacePolizaColectivo` guarda solamente claves HMAC y metadatos técnicos seguros en columnas consultables. El token firmado, DTO, personas, póliza, grupo, contactos, riesgos, planes, valores y grupos funcionales viven dentro de `encrypted_snapshot`. El checksum detecta alteraciones y el perfil/backend/origen se validan antes de restaurar.

La caché es L1. La base local cifrada es L2. Vaciar o reiniciar la caché no produce una consulta a Zoho mientras el Workspace siga vigente.

La vigencia se controla con `COLECTIVOS_POLICY_WORKSPACE_TTL_SECONDS` (8 horas por defecto). La variable anterior de preparación se conserva para compatibilidad, pero ya no define por sí sola la durabilidad del Workspace.

## Contenido canónico

- empresa o persona de origen y sus datos funcionales confirmados;
- póliza, tomador, estado, ramo, código, aseguradora, layout y vigencias;
- forma de pago, frecuencia, cuotas y calendario;
- planes y valores económicos confirmados;
- asegurados, afiliados, beneficiarios y parentescos;
- riesgos y atributos confirmados de inmueble o vehículo;
- warnings, indicadores y condición de truncamiento defensivo;
- grupos funcionales consolidados por HMAC;
- métricas seguras de la última hidratación;
- historial seguro de sincronizaciones.

Solicitudes, respuestas, accesos, comparativos y eventos siguen siendo entidades locales normalizadas. El Workspace las agrega al render sin consultar Zoho; no se duplican dentro del snapshot canónico.

## Navegación local

Después de hidratar, estas operaciones no requieren Zoho:

- volver a abrir la ficha;
- desplegar el grupo funcional;
- abrir la vista de grupo compatible;
- generar el Excel de información actual;
- crear o reutilizar una solicitud;
- generar, copiar, abrir, regenerar o revocar un acceso;
- renderizar el miniportal;
- guardar y revisar respuestas;
- generar comparativos y exportaciones;
- mostrar historial y timeline.

## Actualización transaccional

El botón de actualización omite el snapshot vigente, hidrata nuevamente y reemplaza preparación y snapshot dentro de una transacción local. El Workspace anterior no se destruye antes de completar la nueva hidratación; si Zoho falla, no queda un snapshot parcial publicado.

## Agrupación

Las personas se unen exclusivamente mediante referencias confirmadas convertidas a HMAC. Las conexiones entre afiliado, asegurado y beneficiario forman componentes funcionales, de modo que una persona con varios roles se muestra una vez. Los nombres nunca son claves de unión.

## Rendimiento y diagnóstico

Los logs saneados separan fachada, Organization, póliza, relaciones, Contacts, Riesgos, DTO, agrupación, serialización, cifrado, persistencia, restauración, contexto, template, Excel, miniportal y total. No incluyen documentos, nombres, IDs de Zoho, tokens, cuerpos ni criterios.

Medición controlada:

```powershell
python manage.py colectivos_benchmark_workspace `
  --profile production `
  --policy 091000811814 `
  --allow-production-read
```

El comando usa una póliza de la allowlist, es solo lectura en Zoho y no imprime valores funcionales. La localización de control se informa separada de la hidratación del Workspace.

## Operación y rollback

La migración `0007_workspacepolizacolectivo` debe aplicarse antes de habilitar esta versión. No replica Zoho: crea solamente el contenedor local cifrado y regenerable.

No hay escritura en Zoho ni cambios de OAuth, scopes, SDK o `integrations`. El rollback de comportamiento consiste en retirar la ruta de consumo del Workspace mediante código; los snapshots son datos regenerables y pueden expirar sin afectar la fuente de verdad.
