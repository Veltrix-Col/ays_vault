# Reglas de procesamiento SOAT

## Normalización

- Placa: mayúsculas y solo caracteres alfanuméricos.
- Textos categóricos: comparación saneada/normalizada.
- Fechas: conversión tolerante para ordenar y exportar.
- Identificadores: normalización para comparación, no para inventar valores.

## Criterios de Gestión SOAT A&S

Se aplican en este orden efectivo:

1. Vendedor Fonconstruimos y estado Movilidad `EXCLUIDO` → **No gestión** (criterio 8).
2. Póliza Movilidad `VIGENTE` y asegurado `EXCLUIDO` → **No gestión** (criterio 9).
3. Vendedor contiene `FONCONSTRUIMOS` → **Gestión A&S** (criterio 1).
4. Asegurado `CANCELADO` y motivo `POR VENTA` o `POR CAMBIO DE INTERMEDIARIO` → **No gestión** (criterio 5).
5. Vendedor contiene `FABIO ARANGO` → **No gestión** (criterio 4).
6. Existe estado y no contiene `ACTIVO` → **No gestión** (criterio 6).
7. Líder contiene `ANGELINA` → **Autogestión cliente** (criterio 3).
8. Estado contiene `ACTIVO` → **Gestión A&S** (criterio 2).
9. Resto → valor en blanco (criterio 7).

El número de criterio no coincide con su posición de evaluación. A&S debe confirmar nombres propios y prioridades antes de cambiar reglas.

## ID de carga

Usa primero el ID del asegurado SOAT seleccionado; si está vacío, usa el ID Movilidad seleccionado. No se crea un ID nuevo.

## Integridad

Se exige una fila final por placa, sin duplicados, universo 1:1 con las placas fuente útiles y coherencia del ID de carga y criterios recalculados.
