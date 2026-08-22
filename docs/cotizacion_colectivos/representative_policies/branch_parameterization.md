# Parametrización de ramos

| Código matriz | Nombre funcional | Área | Slug |
|---|---|---|---|
| 91 | Salud colectivo | Colectivos | `salud-colectivo` |
| 86 | Exequial colectivo | Colectivos | `exequial-colectivo` |
| 28 | Hogar colectivo | Colectivos | `hogar-colectivo` |
| 83 | Vida grupo deudores | Colectivos | `vida-grupo-deudores` |
| 40 | Movilidad colectivo | Colectivos | `movilidad-colectivo` |

La configuración runtime cerrada se implementó en `cotizacion_colectivos/branches.py` después de aprobar la radiografía. Usa coincidencia exacta del valor de ramo en Zoho, código, slug, estructura y reglas especiales. Los cinco ramos son los únicos habilitados; cualquier otro valor permanece pendiente de clasificación.

El código 86 compartido nunca basta para clasificar: se exige el valor exacto `Exequial colectivo`.

La Matriz de Ramos se utilizó únicamente como catálogo funcional; no se usa para buscar registros en Zoho.
