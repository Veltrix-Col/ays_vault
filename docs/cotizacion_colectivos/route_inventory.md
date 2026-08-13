# Inventario de rutas del motor de Colectivos

## Puntos de entrada visibles

- `/cotizacion-colectivos/solicitudes-renovaciones/`.
- `/cotizacion-colectivos/invitaciones-aseguradoras/`.
- Cada entrada tiene una ruta de búsqueda de cliente con modo fijado por el
  servidor y rutas de ficha cliente/póliza que conservan ese contexto.

## Motor compartido

- `/cotizacion-colectivos/`: entrada histórica; usa Solicitudes y Renovaciones.
- `/cotizacion-colectivos/clientes/buscar/`: compatibilidad de búsqueda común.
- `/empresas/<token>/` e `/individuos/<token>/`: selección de póliza.
- `/polizas/<token>/`: detalle de póliza y centro operativo.
- `/polizas/<token>/enlace/`: generación directa.
- `/polizas/<token>/enlace/revocar/`: revocación directa.
- `/polizas/<token>/actualizar/`: única acción que fuerza nueva lectura Zoho.
- `/polizas/<token>/excel/`: Excel desde la copia local cifrada.
- `/notificaciones/`: respuestas informativas de clientes.
- `/respuestas/<public_id>/<version>/`: detalle local de la respuesta.
- `/solicitudes/colectivos/externa/<token>/` y `/externa/portal/`: entrada y
  miniportal externo desde snapshot local.

## Compatibilidad no enlazada desde el flujo principal

Se conservan temporalmente para datos y URLs históricas:

- `solicitudes/construir/...`;
- `solicitudes/` y `solicitudes/<public_id>/`;
- edición, transición y regeneración de snapshot;
- preparación de acceso por formulario/correo;
- revisión y exportaciones históricas de respuestas.

Estas rutas no aparecen en el encabezado, ficha de entidad ni detalle de póliza. No son
un requisito para generar, copiar, responder o recibir la notificación. Su
retirada física requiere una iteración independiente de retención y
compatibilidad de datos.

## Diagnóstico y administración

Los comandos de benchmark, descubrimiento y profiling permanecen fuera de la
navegación web y conservan sus confirmaciones explícitas de lectura real.
