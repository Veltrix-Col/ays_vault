# Portal Colectivos: arquitectura funcional vigente

## Estado y navegación

La aplicación Django conserva un solo motor técnico (`cotizacion_colectivos`).
El Banco sólo organiza entradas visuales; no duplica servicios, Workspace,
Snapshot, caché, seguridad ni componentes.

```text
Banco de Herramientas
├── Área Cartera
│   └── CardManager
├── Área Operaciones
│   └── SOAT
└── Área Colectivos
    ├── Novedades
    ├── Cotización Individual
    ├── Invitaciones a Aseguradoras
    └── Conciliador de Facturación
```

No existe una tarjeta adicional llamada “Colectivos”. La búsqueda del Banco es
local, insensible a mayúsculas y acentos, reconoce alias, informa el área y
permite flechas, Enter y Escape. En cada subhome sólo aparecen sus herramientas.
El subhome de Colectivos presenta además, en un bloque visual separado, el
**Centro operativo / Bandeja de solicitudes**. No cuenta como quinta herramienta.

Novedades, Cotización Individual e Invitaciones entran al motor compartido con
un modo cerrado del lado servidor. Cliente, ramo y póliza conservan ese modo;
no hay caída silenciosa a Novedades. Conciliador mantiene su flujo existente.

## Ficha de cliente y ramo

La ficha operativa presenta primero cliente, búsqueda local de pólizas y ramos
provenientes del Workspace. Dentro de cada ramo muestra pólizas operables o
vigentes con sus referencias completas. Las históricas o no operables quedan
en una sección secundaria plegable. Contactos y referencias técnicas se
mantienen disponibles mediante revelado progresivo, no como jerarquía principal.

La búsqueda usa exclusivamente datos ya hidratados (póliza, ramo, placa,
documento, asegurado y referencias disponibles), por lo que no consulta Zoho
por cada tecla. Cambiar de herramienta con un Workspace vigente conserva
`remote_queries=0`.

Invitaciones ya ofrece preview tabular y descargas por aseguradora desde el
Workspace de una póliza. La entrada consolidada directamente desde un ramo con
múltiples pólizas sigue **PARCIAL**: no se fusionan pólizas sin una regla de
negocio que indique cuándo consolidar y cuándo separar.

## Novedades y enlaces externos

Novedades captura exclusivamente Ingreso o Retiro; no decide cortes, primas,
facturación, asegurabilidad ni aceptación de la aseguradora. El ingreso usa un
panel lateral compacto, campos de tamaño normal, dos columnas cuando hay ancho
y una columna en móvil, sin scroll horizontal.

Los accesos externos vencen exactamente en
`created_at + COLECTIVOS_EXTERNAL_LINK_TTL_SECONDS` (172800 segundos por
defecto), sin redondeo al final del día. La fecha límite del expediente sólo
puede acortar esa vigencia.

El token no autoriza el formulario. Al abrirlo se emite un OTP al destinatario
registrado, se conserva sólo su hash, expira, limita intentos y crea una cookie
firmada, aislada y `HttpOnly` tras una verificación satisfactoria. La analista
puede corregir el correo antes de generar el enlace; ello no modifica el dato
maestro en Zoho.

## Cotización Individual

Cotizar no equivale a ingresar o emitir. El acceso parte del contexto firmado
cliente → ramo → póliza → afiliado/persona, utiliza el mismo límite exacto de
48 horas y exige OTP. El acceso queda enlazado con la respuesta y marcado como
usado al completarse.

Movilidad conserva personas y vehículos repetibles. Un vehículo 0 km puede
enviarse sin placa; para uno no 0 km la placa es obligatoria en navegador y
servidor.

Salud usa una colección de asegurados, distingue afiliado principal/familiar y
pregunta por cobertura vigente, entidad y fecha final de forma condicional.
Quedan fuera enfermedades, antecedentes, hábitos y toda Declaración de
Asegurabilidad.

Para el contexto “Fondo de Empleados Construimos Sueños” / Fonconstruimos se
solicita **Empresa a la cual pertenece** como texto obligatorio, editable,
normalizado y validado en servidor. Es una declaración del cliente, no un ID ni
un lookup Zoho. No existe todavía un catálogo estructurado demostrado
Fonconstruimos → empresas.

## Invitaciones, correo y buzón

El catálogo declarativo, maestras inmutables, chunking, múltiples archivos y ZIP
se mantienen. El preview muestra columnas legibles y filas que alimentarán cada
plantilla. Se puede generar una aseguradora o todas. Los datos salen del mismo
Workspace vigente; la pantalla no agrega lecturas remotas.

“Preparar correo” usa `mailto:` con asunto y cuerpo para revisión humana. No
envía desde Django y advierte que los archivos no se adjuntan automáticamente.
Adjuntar y auditar desde Outlook requiere una integración Graph independiente.

Existe una sola Bandeja canónica en `/cotizacion-colectivos/solicitudes/`. La
ruta histórica `/notificaciones/` se conserva únicamente como redirección. La
Bandeja consolida solicitudes de Novedades y accesos/respuestas de Cotización
Individual, muestra un solo estado actual del enlace y prioriza `Respondida`
como trabajo pendiente. Los números completos de póliza se leen exclusivamente
del Snapshot cifrado en pantallas internas; el `COL-YYYY-XXXXXXXX` se conserva
como referencia técnica secundaria. El expediente coloca la respuesta y el
estado Zoho en primer plano, y mantiene eventos/auditoría en un bloque cerrado.
Las respuestas individuales siguen usando rutas firmadas; no se exponen IDs
arbitrarios.

## Tasks, aceptación y adjuntos

Existe una outbox local cifrada e idempotente por origen, evento y versión, un
builder con allowlist y un dry-run sanitizado. Los únicos campos candidatos
permitidos son `Subject` y `tipo_de_solicitud`, con valores exactos `Ingresos`,
`Retiros` y `Cotización`.

La publicación real está **DESHABILITADA**. Colectivos rechaza Production,
requiere flag y confirmación Sandbox adicionales y, aun con ellas, bloquea la
operación porque el snapshot no demostró layouts/reglas obligatorias. No se hizo
CREATE, UPDATE ni read-after-write.

El proceso posterior a una cotización aceptada queda **PARCIAL**: si la persona
no existe deberá crear una tarea de creación de persona; si existe, una tarea de
creación/asociación de póliza. No se crean Contacts ni Polizas directamente sin
contrato confirmado.

Los adjuntos permanecen locales y cifrados. `ays-zoho-sdk` 1.1.0 no expone una
API pública confirmada para attachments; no se usa HTTP directo.

## Matriz de cierre

### IMPLEMENTADO

- Home por áreas, subhomes y búsqueda local accesible.
- Motor compartido y aislamiento de Novedades/Cotización/Invitaciones.
- Ficha compacta por ramos, vigentes primero y búsqueda local.
- OTP y vigencia exacta de 48 horas para Novedades y Cotización Individual.
- correo destino editable, Autos/0 km, Salud MVP y empresa declarada Fonconstruimos.
- preview tabular, descarga individual/ZIP, preparación `mailto:` y Buzón básico.
- outbox, allowlist, dry-run y guardas de Tasks con escritura deshabilitada.

### PARCIAL

- Invitaciones consolidadas desde ramo sin seleccionar una póliza.
- detalle operativo completo de respuestas y acción posterior a aceptación.
- Designación de Beneficiarios SURA: no se implementó el formulario repetible
  ni la suma 100%; su `tipo_de_solicitud` sigue sin mapping inequívoco.

### PENDIENTE A&S

- catálogo estructurado Fonconstruimos → empresas.
- Task representativa por tipo, layout, Owner, Status, Responsable, Área y SLA.
- semántica de `What_Id`, `Who_Id`, `ID_Tomador`, `ID_asegurado` e
  `ID_Riesgos1_task`.
- clasificación Tasks para Designación de Beneficiarios.

### PENDIENTE SDK

- superficie pública y auditada de attachments Zoho.

### FUTURO

- Outlook/Graph, publicación automática de Tasks y creación directa de entidades
  sólo después de aprobar sus contratos.
