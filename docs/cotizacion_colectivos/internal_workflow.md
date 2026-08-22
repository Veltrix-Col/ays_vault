# Base interna de Cotización – Colectivos

## Alcance

Esta fase implementa la consulta funcional de solo lectura en Zoho y el expediente operativo local. No existe miniportal, enlace externo, envío de correo, carga de archivos, respuesta de cliente ni escritura en Zoho.

```text
Vista interna
  → servicios de Cotización – Colectivos
    → integrations.zoho.get_zoho(perfil global)
      → facade desacoplada
        → SDK/REST de solo lectura
          → Zoho CRM V8

Expediente interno
  → modelos Django locales
    → snapshot cifrado + filas normalizadas
    → eventos + notificaciones
```

## Parametrización cerrada

La parametrización runtime está en `cotizacion_colectivos/branches.py`. Solo reconoce coincidencias exactas:

| Código | Ramo | Estructura |
|---|---|---|
| 91 | Salud colectivo | Grupo de personas |
| 86 | Exequial colectivo | Grupo familiar |
| 28 | Hogar colectivo | Inmueble |
| 83 | Vida grupo deudores | Grupo de deudores/obligaciones |
| 40 | Movilidad colectivo | Vehículo |

El código 86 no clasifica por sí solo: `Exequial individual` no coincide con el valor permitido `Exequial colectivo`. Los valores no reconocidos quedan como “Ramo pendiente de clasificación” y no habilitan expedientes.

## Fichas y relaciones

La ficha de empresa o individuo agrupa las pólizas por ramo. Los roles se derivan exclusivamente de lookups presentes en `Riesgos1`; no hay uniones por nombre. La ruta principal sigue siendo:

```text
Contacts → Riesgos1 → Polizas
                    → Riesgos
```

La relación directa `Polizas.Tomador_principal1 → Contacts` continúa separada y marcada como parcial. Al existir varios IDs, pólizas y riesgos se resuelven mediante COQL interna fija por lotes; el navegador no aporta módulos, campos ni consultas.

## Detalle de póliza y grupo actual

El detalle presenta ramo, aseguradora, vigencia, renovación, modo de pago, frecuencia, cuotas, conteos y advertencias. `Prima`, `Pago_total`, `Pago_total_Seg_n_la_forma_de_pago_Valor_asegura` y `Pago_EMPLEADO_Sin_IVA` conservan nombres y semánticas distintas.

El grupo actual tiene límite defensivo de 200 registros por consulta. Vida grupo deudores debe considerarse parcial si supera ese límite. Hogar y Movilidad complementan la vista con los riesgos vinculados; no se renderizan todos los campos multi-layout.

## Excel actual

La descarga requiere POST, CSRF y permiso `cotizacion_colectivos.export_excel`. El libro se genera en memoria y no se persiste. Contiene:

1. `Información actual`.
2. `Información de póliza`.
3. `Metadatos` oculta.

Se neutralizan celdas que empiezan por `=`, `+`, `-` o `@`; documentos e identificadores se fuerzan a texto; se aplican filtros, panel congelado, anchos limitados y respuesta `no-store`. El Excel es una fotografía operativa, no la fuente principal.

## Expediente local

`SolicitudColectivo` guarda referencias HMAC, el token firmado de póliza cifrado, etiqueta operativa, ramo, tipo, estado, responsable, fechas, perfil y snapshot cifrado. `SolicitudColectivoRegistro` combina campos comunes normalizados con un payload específico cifrado y versionado. No replica la base de Zoho.

Identificador: `COL-AAAA-XXXXXXXX`, independiente del PK y sin NIT, póliza o nombre.

Tipos habilitados inicialmente:

- Actualización de datos.
- Renovación.

Una solicitud activa duplicada del mismo tipo y póliza se rechaza.

## Navegación operativa desde la póliza

La ficha de una póliza colectiva clasificada es el punto de entrada del flujo interno. Desde allí el usuario autorizado puede ver el grupo actual, descargar el Excel actual mediante POST, crear una solicitud de actualización o renovación y abrir los expedientes locales ya vinculados a la misma póliza, entidad de origen y perfil Zoho. La asociación usa hashes con clave de la referencia firmada; nunca compara solamente el número visible de póliza.

Si ya existe una solicitud activa del mismo tipo, la interfaz ofrece abrirla y el servicio vuelve a impedir el duplicado dentro de la transacción. Una póliza sin clasificación confirmada no puede exportarse ni originar solicitudes. Los permisos ausentes se explican con acciones deshabilitadas o mensajes funcionales, sin exponer detalles técnicos.

El acceso externo se administra únicamente desde el expediente: borrador → preparar envío → generar acceso. El enlace completo se muestra una sola vez en la respuesta inmediata de generación y solo se conserva su hash. Un enlace vigente puede regenerarse —revocando el anterior— o revocarse explícitamente mediante POST y CSRF. Un enlace perdido no puede recuperarse; debe regenerarse.

## Estados

Transiciones habilitadas en la fase interna:

```text
BORRADOR → LISTA_PARA_ENVIAR → EN_REVISION → APROBADA → CERRADA
    └──────────────→ CANCELADA ←──────────────┘
EN_REVISION → REQUIERE_CORRECCION → EN_REVISION
```

Los estados externos están definidos, pero no se activan hasta construir el miniportal. Una solicitud cerrada o cancelada no puede modificarse.

## Bandeja, notificaciones e historial

La bandeja pagina 25 expedientes y conserva en la paginación los filtros por texto seguro, entidad, estado, ramo, tipo, responsable, fechas, asignación propia y advertencias. Los eventos registran actor, transición, fecha, correlación y metadata saneada. Las notificaciones son por usuario, cuentan únicamente no leídas y usan clave de deduplicación.

Mientras el expediente permanezca en `BORRADOR`, un usuario con permiso puede actualizar responsable, fecha límite y notas internas cifradas. También puede regenerar el snapshot con confirmación explícita: se vuelve a consultar la misma póliza mediante su token protegido, se exige el mismo perfil y ramo, y el snapshot y sus filas se reemplazan atómicamente aumentando la revisión. Esta operación no escribe en Zoho.

## Permisos

La lectura pública/intranet de las herramientas conserva la política delegada existente. Las acciones internas exigen usuario Django activo y permiso específico; los superusuarios activos mantienen bypass administrativo. Se crearon permisos separados para ver, crear, editar borradores, asignar, aprobar, cerrar, cancelar, exportar, ver datos económicos/personales y gestionar notificaciones.

Para QA sin usar superusuario, un administrador puede asignar desde Django admin, directamente al usuario o a un grupo, los permisos mínimos según la función:

- `export_excel`: descargar Excel actual y plantillas autorizadas.
- `create_requests`: crear expedientes y prepararlos para envío.
- `view_requests`: listar y abrir expedientes asociados.
- `generate_external_access`: generar el primer acceso externo.
- `regenerate_external_access`: rotar un acceso y revocar el anterior.
- `revoke_external_access`: revocar un acceso vigente sin generar otro.
- `send_requests`: enviar la invitación durante la generación o regeneración.
- `view_personal_data` y `view_economic_data`: habilitar las vistas que correspondan a esas categorías; no se conceden implícitamente con los permisos anteriores.

Los permisos deben asignarse explícitamente; esta implementación no amplía roles globales ni reutiliza automáticamente permisos de CardManager.

## Seguridad

- Zoho: exclusivamente lectura mediante `integrations.zoho`.
- Perfil: selector global validado; no hay selección desde navegador ni fallback.
- IDs de detalle: tokens firmados, tipados y expiran en 15 minutos.
- Referencias locales: HMAC; snapshot y payload específico: cifrados.
- Cambios locales: POST + CSRF + transacción atómica + permiso.
- Logs y auditoría: métricas y categorías, sin documentos, nombres, tokens o cuerpos.
- Excel: fórmula neutralizada, límite defensivo y sin caché.

## QA manual

1. Aplicar migraciones en el entorno local.
2. Iniciar Django con el perfil deseado y validar conexión por el comando administrativo existente.
3. Abrir `/cotizacion-colectivos/`.
4. Buscar una empresa o individuo autorizado sin documentar el valor usado.
5. Abrir una póliza clasificada, revisar el grupo y descargar el Excel con un usuario autorizado.
6. Crear un expediente marcado como prueba, revisar bandeja, historial y campanita.
7. Recorrer transiciones permitidas y confirmar el rechazo de saltos.
8. Cancelar o archivar el expediente de QA al finalizar.
9. Revisar 320, 375, 768, 1024 y 1440 px; las tablas extensas usan un contenedor desplazable propio sin generar scroll horizontal de página.

## Pendientes del Bloque 3

- Enlace externo y vigencia de token.
- Formulario y borradores del cliente.
- Adjuntos.
- Carga de Excel respondido y comparativo.
- Correos.
- Revisión de respuesta.
- Escritura futura en Zoho, sujeta a una intervención de seguridad y permisos separada.

Los campos de obligación, saldo, entidad acreedora y número de crédito de Vida grupo deudores siguen pendientes de API name confirmado. No se inventaron ni se parametrizaron.
