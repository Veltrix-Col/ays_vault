# Cotización Individual

## Estado y objetivo

Versión demostrable local para capturar solicitudes uno a uno por ramo. No es
un formulario por empresa ni una póliza individual: distingue solicitante,
colectiva/tomador, afiliado, asegurado, beneficiario y riesgo. Zoho permanece
en solo lectura y el envío no crea tareas ni registros CRM.

## Arquitectura

```text
Banco de Herramientas
  → catálogo de ramos (`quotation_forms/catalog.py`)
  → formulario dinámico común
  → validación server-side
  → `CotizacionIndividual` (payload cifrado)
  → `AdjuntoCotizacionIndividual` (archivo cifrado privado)
  → confirmación firmada y temporal
```

La entidad es propia porque `SolicitudColectivo` representa renovaciones y
novedades de pólizas existentes. Reutilizarla habría mezclado estados,
permisos y semántica. Los dos modelos nuevos son mínimos y no replican cliente,
póliza, Workspace ni datos Zoho.

## Ramas parametrizadas

| Ramo | Repetible | Diferenciación inicial | Pendiente funcional |
|---|---|---|---|
| Movilidad / Autos | Vehículos (1–20) | solicitante, contexto y asegurado por vehículo | Fasecolda, coberturas y reglas por producto |
| Salud | Personas (1–20) | afiliado principal, rol y parentesco | reglas médicas, planes y documentos definitivos |
| Vida | Asegurados (1–20) | rol, parentesco y actividad económica opcional | asegurabilidad, amparos y valores |
| Exequial | Grupo familiar (1–20) | afiliado y familiares | beneficiarios y reglas por tomador |
| SOAT | Vehículos (1–20) | afiliado y asegurado son campos independientes | campos definitivos de expedición |

Los campos se definen en objetos inmutables `BranchSchema`, `FieldSchema` y
`RepeatableSchema`; el template no contiene reglas por ramo.

## Flujo

1. Elegir ramo.
2. Diligenciar solicitante y contexto, o usar el buscador unificado para
   precargar un cliente.
3. Agregar, editar o eliminar personas/vehículos.
4. Adjuntar soportes.
5. Revisar conteos y enviar.
6. Recibir confirmación no sensible.

La ficha de cliente genera un contexto opaco, firmado y cifrado; no expone NIT
ni ID Zoho. El formulario también funciona sin contexto.

## Persistencia y seguridad

- Payload JSON cifrado con la infraestructura Fernet existente.
- Checksum SHA-256 del ciphertext y hash del contexto.
- Adjuntos PDF/JPG/PNG: máximo 10, límites globales de tamaño, doble extensión
  rechazada, magic bytes verificados, nombres internos aleatorios, contenido
  cifrado y almacenamiento bajo `COLECTIVOS_PRIVATE_ROOT`.
- CSRF, POST, `never_cache`, recibo firmado con caducidad y anti-IDOR.
- Logs agregados sin nombres, documentos, correos, archivos, tokens o IDs Zoho.
- No existe importación de facade ni método de escritura Zoho en este flujo.

## Hook futuro Zoho

La frontera futura será un servicio explícito que lea el payload cifrado ya
validado y cree tareas/personas/riesgos con idempotencia y auditoría. No está
implementado ni autorizado. Antes se deben cerrar mappings, permisos, scopes,
adjuntos y reglas con Colectivos.

## Pendientes para validación A&S

Campos obligatorios y opcionales, documentos por ramo, roles definitivos,
reglas especiales por tomador, campos prefill y destino futuro en Zoho.
