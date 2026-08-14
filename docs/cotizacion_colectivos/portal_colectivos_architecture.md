# Portal Colectivos: arquitectura funcional vigente

## Estado

Esta es la referencia funcional vigente. La aplicación Django conserva el
nombre técnico `cotizacion_colectivos`; no se crearon motores separados.

```text
Portal Colectivos
├── Novedades
│   ├── Ingreso
│   └── Retiro
├── Cotización Individual
│   └── Formulario contextualizado por ramo
└── Invitaciones a Aseguradoras
    └── Catálogo declarativo de plantillas
```

Las tres entradas reutilizan buscador, ficha consolidada, pólizas, Policy
Workspace, Snapshot cifrado, caché L1/L2, tokens firmados, HMAC, anti-IDOR,
servicios y métricas. El modo visual no forma parte de la identidad del
Workspace. Con una preparación vigente, cambiar de herramienta y continuar la
navegación usa datos locales (`remote_queries=0`).

## Novedades

Novedades captura exclusivamente la intención de **Ingreso** o **Retiro**. No
es un editor libre de la póliza y no decide fecha efectiva, corte, prima,
nómina, facturación, asegurabilidad ni aceptación de aseguradora.

- Ingreso: nombre completo, tipo y número de identificación, fecha de
  nacimiento, fecha solicitada de ingreso y observaciones opcionales.
- Retiro: parte de una persona o riesgo confirmado en el Workspace y solicita
  únicamente fecha de retiro y observaciones.
- Empresa, póliza y ramo proceden del contexto firmado; no se vuelven a pedir.

La fecha informada por el cliente es solicitada. A&S determina posteriormente
su aplicabilidad. El resultado es una captura local cifrada, una respuesta y
una notificación con lenguaje “Novedad recibida”.

## Cotización Individual

Cotizar no equivale a ingresar o expedir. El ramo se deriva de la póliza y el
formulario solicita solo información inicial para cotizar. Puede partir de:

- un afiliado ya confirmado mediante referencia HMAC; o
- una persona nueva todavía no registrada, manteniendo como contexto mínimo el
  tomador/empresa y la póliza/ramo.

Los esquemas actuales cubren Movilidad/Autos, Salud, Vida, Exequial y SOAT. Los
grupos de personas y vehículos son repetibles. Un vehículo nuevo o 0 km puede
enviarse sin placa; no se inventa un valor. En SOAT, afiliado, asegurado y
vehículo permanecen conceptos distintos. Los adjuntos son opcionales, se
validan por extensión/MIME/magic bytes y se guardan cifrados junto con la
captura local.

Se mantienen fuera del core los cuestionarios médicos completos, documentos
de expedición y reglas específicas por producto o aseguradora. Los futuros
formularios especiales se resolverán mediante configuración declarativa; no
hay un caso hardcodeado por empresa.

## Invitaciones a Aseguradoras

El ramo ya está determinado por la póliza. El catálogo relaciona ramo,
aseguradora, plantilla, versión, estado, mapping, capacidad y soporte de
partición. La aseguradora vigente no limita las invitadas. Los datos disponibles
se precargan desde el Workspace; los manuales o ausentes quedan vacíos y no
bloquean.

El chunking conserva todos los registros cuando la plantilla lo permite (por
ejemplo, 136 registros con capacidad 21 producen siete archivos). SURA Vida
Grupo en BIFF8 `.xls` sigue catalogada e inactiva: no se convierte de manera
silenciosa a XLSX.

## Renovaciones y compatibilidad

**Renovaciones está fuera del alcance actual.** No aparece en el Banco, en las
acciones de póliza, en breadcrumbs ni en el miniportal vigente. Modelos,
migraciones, estados, formularios y URLs históricos no se borraron porque
conservan trazabilidad y compatibilidad de enlaces existentes. Las rutas bajo
`solicitudes-renovaciones/` y el constructor administrativo se consideran
legacy, no están enlazadas desde el recorrido canónico y deben evaluarse en una
iteración independiente antes de su eliminación física.

## Futuro publicador de tareas Zoho

`cotizacion_colectivos.services.task_publisher` define una frontera local para
una fase futura. La única implementación actual está deshabilitada y falla de
forma explícita. No importa la integración Zoho, no tiene adaptador remoto y no
ejecuta creación, actualización ni upsert.

Flujo futuro, no activo:

```text
respuesta local → construir payload de tarea → publicar en Zoho
```

Antes de habilitarlo deben confirmarse módulo/tipo de tarea BYB, relaciones,
campos, adjuntos, permisos, scopes y controles operativos. En el estado actual
Zoho continúa estrictamente en solo lectura.

## Inventario pendiente de Zoho Forms

Queda pendiente recibir el inventario funcional de A&S. Debe registrar ramo,
nombre del Form, alcance general o por empresa, campos, adjuntos, si crea tarea
y observaciones. Ese inventario no bloquea los formularios estándar actuales.

