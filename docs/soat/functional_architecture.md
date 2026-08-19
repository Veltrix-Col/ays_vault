# Arquitectura funcional de SOAT

## Flujo web

1. El operador abre `/soat/` y selecciona un `.xlsx`.
2. El formulario valida extensión, tamaño, estructura ZIP y encabezados mínimos.
3. El procesador lee la hoja compatible (preferencia histórica `Sheet0`, con resolución por estructura).
4. Se normalizan encabezados, fechas, identificadores y placa.
5. Las filas se separan en `Ramo (Póliza) == SOAT` y no-SOAT (Movilidad).
6. Cada conjunto se selecciona independientemente por placa.
7. Se construyen formato y trazabilidad, se validan y se exportan dos libros con las hojas separadas.
8. El navegador recibe el XLSX; los temporales se eliminan al terminar.

## Decisiones funcionales

La selección conserva la fila ganadora completa: no mezcla columnas entre candidatos. SOAT y Movilidad sí pueden provenir de filas distintas porque representan universos independientes. Las placas sin valor útil no se incluyen en el universo final.

## Acceso

La URL es pública respecto de CardManager y no genera auditoría Vault. La política `TOOLS_ACCESS_MODE` puede limitar globalmente la herramienta a una intranet confiable, pero el módulo no implementa login propio.
