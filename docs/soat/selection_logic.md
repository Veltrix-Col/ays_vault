# Lógica de selección por placa

Para cada placa y para cada universo (SOAT o Movilidad) se ordenan los candidatos por:

1. `Estado de la póliza == VIGENTE` primero.
2. `Estado asegurado` que contiene `ACTIVO` primero.
3. `Póliza - Fecha fin vigencia` más reciente primero.
4. `_orden_origen` mayor primero (última fila fuente), manteniendo estabilidad.

La primera fila ordenada gana y se conserva completa. No se combinan campo a campo candidatos de un mismo universo. La selección SOAT no determina la Movilidad ni viceversa.

## Duplicados

Varias filas de una placa se conservan en hojas de selección/trazabilidad, pero el formato final tiene una fila por placa. La trazabilidad marca multiplicidad para revisión. Placas normalizadas iguales se consideran la misma entidad técnica.

## Diferencia con el proceso anterior

El código declara independencia frente a `informe_renovaciones.py`, pero ese archivo no está en el repositorio. Solo puede afirmarse que el algoritmo vigente usa el orden anterior; cualquier comparación 1:1 con un proceso externo requiere archivos/resultados controlados de A&S.
