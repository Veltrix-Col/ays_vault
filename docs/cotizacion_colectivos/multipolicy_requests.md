# Solicitudes multipóliza de Cotización – Colectivos

> Documento histórico de compatibilidad. El constructor no forma parte de la
> navegación ni del recorrido operativo vigente. Consulte
> `simple_policy_access.md` y `route_inventory.md`.

## Decisiones de contrato

El expediente parte de una empresa o persona y puede incluir entre una y diez pólizas colectivas clasificadas. Cada póliza conserva su snapshot cifrado, checksum, ramo, vigencia, aseguradora, advertencias y catálogo versionado de ajustes. El expediente produce un único acceso externo y una única respuesta final.

No existe evidencia local suficiente para identificar de forma segura una modalidad patronal, voluntaria o mixta. La modalidad se almacena como `NO_DETERMINADA`; no se infiere a partir de modo de pago, frecuencia, plan ni valores económicos. El modelo queda preparado para una futura clasificación sustentada en metadata y validación funcional.

Los ramos inicialmente seleccionables continúan limitados a 91, 86, 28, 83 y 40. Los aliases de Vida grupo deudores se resuelven mediante la clasificación cerrada existente. Las pólizas ambiguas permanecen visibles como pendientes, pero no son seleccionables.

## Arquitectura

```text
Empresa o individuo
  -> constructor de solicitud
    -> SolicitudColectivo (expediente)
      -> SolicitudColectivoPoliza (1..10)
        -> SolicitudColectivoRegistro (filas precargadas)
      -> AccesoExternoSolicitudColectivo (uno vigente)
      -> RespuestaSolicitudColectivo (versionada)
        -> CambioSolicitudColectivo (ligado a una póliza)
```

`PolicyService.group()` es la fuente enriquecida. Resuelve los lookups confirmados `Asegurado`, `Contacto_facturaci_n_dividida_colectivas` y `Beneficiario` por lotes, además de los riesgos. El mismo DTO alimenta grupo visible, Excel actual, snapshot y registros normalizados. Los documentos completos solo quedan en memoria o dentro del snapshot/payload cifrado; el miniportal muestra la versión enmascarada.

Para origen persona, el servicio filtra por el ID técnico firmado del individuo antes de crear el snapshot. No incluye el grupo empresarial completo ni une registros por nombre.

## Ajustes

El catálogo tiene versión 1. Están implementados `SIN_CAMBIOS`, `INCLUSION`, `RETIRO` y `MODIFICACION`; los demás códigos funcionales existen como reserva no habilitada. `SIN_CAMBIOS` se incorpora siempre como operación neutral. El cliente no puede usar un ajuste que no haya sido habilitado para la póliza: la regla se valida tanto al importar Excel como al guardar la respuesta.

## Migración y compatibilidad

La migración `0006_solicitud_multipolicy` es incremental. Crea la entidad intermedia y las referencias opcionales desde registros y cambios. El backfill crea una subpóliza por cada solicitud histórica reutilizando, sin descifrar, el token y snapshot existentes; después enlaza sus registros y cambios. No elimina campos heredados del encabezado, de modo que enlaces, respuestas, revisiones, eventos, adjuntos y exportaciones previas continúan resolviéndose.

Las plantillas Excel antiguas con una hoja `Novedades` siguen siendo aceptadas. Las nuevas solicitudes multipóliza usan una hoja segura y única por póliza. El mapa hoja→posición forma parte de los metadatos firmados, por lo que no puede reasignarse una fila a otra póliza sin invalidar la plantilla.

## Constructor y enlace

Desde la ficha existe un solo botón **Crear solicitud al cliente**. La pantalla progresiva permite seleccionar pólizas, ajustes por póliza, objetivo, fecha límite, instrucciones y confirmación del snapshot. Solo se consultan en detalle las pólizas seleccionadas. Al finalizar se crea el expediente, se genera un solo acceso y se muestra inmediatamente; el correo es opcional.

La confirmación del snapshot es obligatoria. Las ausencias de nombre o identificación se cuantifican por póliza y quedan como advertencias agregadas, sin registrar valores personales.

## Miniportal y Excel

El miniportal navega por póliza y precarga nombre, identificación enmascarada, contacto autorizado, rol, estado, plan, parentesco, fechas y riesgo cuando existen. Cada inclusión identifica de manera cerrada una subpóliza del mismo expediente. Hay un solo guardado de borrador y un solo envío final.

El workbook contiene una hoja por póliza, resumen de pólizas, instrucciones, catálogos y metadatos. Dos pólizas del mismo ramo reciben nombres de hoja diferentes. Web y Excel terminan en `save_response`; no existe un modelo paralelo.

Los nombres de descarga se generan con una utilidad central: normalizan tildes y caracteres prohibidos, limitan longitud y agregan fecha/hora. Nunca contienen NIT, documento ni token.

## Seguridad y rendimiento

- Zoho permanece en solo lectura y se consume exclusivamente a través de `integrations.zoho`.
- No se añaden métodos de escritura, scopes, OAuth ni persistencia de datos Zoho.
- Las referencias de entidad y póliza siguen firmadas; los valores sensibles locales siguen cifrados.
- La vinculación póliza/fila se valida contra el expediente para evitar IDOR.
- Los formularios usan POST y CSRF; las respuestas externas mantienen `no-store`.
- Los contactos y riesgos se resuelven por lotes. El constructor solo carga grupos de las pólizas seleccionadas.
- El modo público interno y el actor técnico permanecen como estaban configurados.

## QA y pendientes

Validar visualmente en 320, 375, 768, 1024, 1366, 1440 y 1920 px: constructor, miniportal, Excel preview y bandeja. En móvil el scroll horizontal debe quedar limitado a las tablas.

Pendientes funcionales:

- confirmar en fuentes oficiales de A&S el campo de modalidad patronal/voluntaria/mixta;
- parametrizar y habilitar los ajustes reservados por ramo;
- definir campos críticos cuya ausencia deba bloquear, en lugar de requerir confirmación;
- ejecutar QA real de solo lectura con las cinco pólizas autorizadas cuando el entorno de pruebas esté disponible;
- evaluar una cola real antes de describir generación en segundo plano.

## Rollback

El rollback de interfaz puede retirar el acceso al constructor multipóliza manteniendo las rutas históricas. La migración conserva los campos heredados y no debe revertirse en producción si ya existen expedientes multipóliza. Ante una incidencia, detener nuevas creaciones, conservar los datos y volver temporalmente al flujo de una póliza; no eliminar subpólizas ni ejecutar la reversa de datos.
