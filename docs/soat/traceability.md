# Trazabilidad del proceso SOAT

La hoja `Trazabilidad` explica por placa:

- cantidad de candidatos SOAT y Movilidad;
- fila/póliza/ID seleccionados de forma funcional;
- criterio aplicado a Gestión SOAT A&S;
- indicador de múltiples candidatos/revisión.

El encabezado HTTP `X-SOAT-Summary` contiene un JSON codificado en Base64 con conteos agregados y duración: registros fuente, placas únicas, cobertura SOAT/Movilidad, razones, múltiples candidatos y distribución de gestión/criterios. No es firma ni cifrado y no debe incluir datos personales.

La aplicación no conserva ejecuciones, usuario, fuente ni resultado en base de datos y no genera `AuditEvent` de Vault. La trazabilidad viaja dentro del archivo descargado. Por tanto, reconstruir quién ejecutó un informe depende de controles externos (proxy, servidor o custodia del archivo), no de SOAT.

Para soporte use solo timestamp, tamaño, conteos y categoría de error; no copie filas reales a logs o tickets.
