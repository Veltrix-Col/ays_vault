# Cotización individual contextual

## Estado y objetivo

Implementada como un recorrido contextual sobre una póliza colectiva existente.
No existe un formulario libre por ramo como entrada funcional: el usuario busca
una empresa o persona, abre una póliza y genera un enlace externo para una
persona confirmada o una persona nueva.

Zoho permanece en solo lectura. La respuesta se guarda localmente cifrada y
crea una notificación informativa para el usuario que generó el enlace; no crea
tareas ni registros en CRM.

## Flujo

```text
Cliente → póliza → persona conocida o nueva → enlace firmado
        → formulario externo por ramo → respuesta cifrada → notificación local
```

1. La búsqueda y la ficha consolidada usan el motor común de Colectivos.
2. La póliza se abre desde su token firmado.
3. Un afiliado conocido se deduplica con referencias HMAC del Snapshot; nunca
   por nombre o documento. La opción persona nueva conserva el contexto de
   empresa/tomador y póliza/ramo sin inventar una relación CRM.
4. El contexto externo incluye, cifrados y firmados, el token de póliza, tipo de
   entidad, referencia HMAC del afiliado, ramo y versión de esquema.
5. El formulario precarga únicamente datos presentes en el Workspace y bloquea
   su edición cuando son contexto confirmado.
6. El cliente completa los campos faltantes y envía la respuesta con CSRF.
7. El servidor valida de nuevo la póliza y el ramo. Cuando existe afiliado,
   valida además que su referencia HMAC pertenezca al mismo Workspace.

La entrada histórica `/cotizacion-colectivos/cotizacion-individual/formulario/`
redirige al buscador en GET y no admite POST. No es parte del recorrido visible.

## Ramas parametrizadas

| Código o criterio | Esquema | Grupo repetible | Pendiente funcional |
|---|---|---|---|
| 40 | Movilidad | Vehículos | Fasecolda, coberturas y reglas por producto |
| 91 | Salud | Personas | reglas médicas, planes y documentos definitivos |
| 83 | Vida | Asegurados | asegurabilidad, amparos y valores |
| 86 | Exequial | Grupo familiar | beneficiarios y reglas por tomador |
| Nombre SOAT confirmado | SOAT | Vehículos | campos definitivos de expedición |

Los campos se definen en objetos inmutables `BranchSchema`, `FieldSchema` y
`RepeatableSchema`; el navegador no selecciona libremente el esquema.

## Reutilización local

- La identidad L1/L2 del Workspace depende del perfil y de la póliza, no del
  modo funcional.
- Con Workspace vigente, abrir la ficha individual y generar/abrir el enlace no
  inicializa la fachada ni consulta Zoho.
- Si L1 falla, se restaura el Snapshot cifrado de base de datos antes de intentar
  una hidratación remota.
- La expiración o una actualización explícita desde Zoho son las únicas causas
  normales de reconstrucción remota.

## Persistencia y seguridad

- `CotizacionIndividual` conserva payload JSON cifrado, checksum SHA-256 y hash
  del contexto.
- `AdjuntoCotizacionIndividual` guarda contenido cifrado con nombre interno
  aleatorio bajo el almacenamiento privado configurado.
- `NotificacionCotizacionIndividual` registra solo el aviso local y la relación
  con la respuesta; no replica datos de Zoho.
- Adjuntos PDF/JPG/PNG: máximo 10, límites globales, doble extensión rechazada y
  magic bytes verificados.
- Tokens temporales, firma, cifrado, anti-IDOR, CSRF, `no-store` y logs agregados
  sin nombres, documentos, correos, tokens o IDs Zoho.
- No existe importación de SDK/backend ni método de escritura Zoho en el flujo.

## Pendientes para validación A&S

Campos definitivos por ramo, documentos, reglas especiales por tomador,
equivalencias de planes/coberturas y destino futuro de la respuesta. Cualquier
escritura posterior en Zoho requerirá una fase separada, scopes y autorización
expresos.
