# Operación de SOAT

## Procedimiento periódico

1. Conservar intacto el exporte fuente autorizado.
2. Verificar que corresponde al periodo y organización correctos.
3. Cargarlo una sola vez y esperar la descarga.
4. Revisar resumen, conteos y trazabilidad de múltiples candidatos.
5. Comparar placas fuente/final y revisar criterios 6/7/en blanco.
6. Custodiar o eliminar fuente/salida según política A&S.

## Soporte

Solicitar nombre de hoja, dimensiones, encabezados, timestamp y error; nunca filas reales. Para un fallo, trabajar sobre copia controlada, validar que sea XLSX no macro y revisar cambios de columnas. No “corregir” manualmente la fuente sin conservar evidencia.

## Recuperación

El proceso es determinista respecto de la fuente y reglas: repita con el archivo original. No existe estado local que restaurar. Si el worker termina, `TemporaryDirectory` intenta limpiar; monitoree el directorio temporal por incidentes del proceso/host.

## Controles de calidad

Una fila por placa, cinco hojas, candidatos trazables, ID CARGA coherente, fórmula neutralizada y ausencia de hipervínculos. A&S debe aprobar los nueve criterios y la clasificación amplia de no-SOAT como Movilidad.
