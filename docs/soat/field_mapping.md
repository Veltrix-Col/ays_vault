# Mapeo de campos SOAT

## Campos fuente reconocidos

| Grupo | Campos |
|---|---|
| Identificación | `Placa`, `ID de registro (Asegurado)` |
| Responsables | `Líder Comercial`, `Analista`, `Vendedor`, `Responsable SOAT` |
| Entidades/personas | `Empresa`, `Tomador (Póliza)`, `Nombre completo`, `Número ID`, `Afiliado` |
| Póliza | `Ramo (Póliza)`, `Póliza (Póliza)`, `Aseguradora (Póliza)`, `Estado de la póliza` |
| Riesgo/estado | `Estado asegurado`, `Motivo cancelación`, `Fecha renovación SOAT (Riesgo)`, `Póliza - Fecha fin vigencia` |
| Contacto | cuatro campos de correo comercial/asegurado/afiliado/general |
| Derivado previo | `Gestión SOAT A&S` (se recalcula para salida) |

## Campos de salida

El formato consolida placa; líderes Movilidad/SOAT; vendedor; empresa; gestión; fechas y datos seleccionados de póliza SOAT; ramo/póliza/estado/motivo y datos seleccionados de Movilidad; correos; e IDs SOAT, Movilidad e `ID CARGA`.

## Origen y derivación

- SOAT y Movilidad: copia de la fila ganadora de cada universo.
- Gestión: derivada por nueve reglas.
- ID CARGA: preferencia SOAT y fallback Movilidad.
- Criterio y multiplicidad: derivados para trazabilidad.

No se corrigen valores de negocio ni se completan por coincidencia textual entre personas. Los nombres de columnas deben coincidir con los aliases codificados; cambios del reporte fuente pueden romper el proceso.

## Privacidad

Nombre, documento, correo, póliza e IDs pueden ser personales/confidenciales. El informe los procesa porque son parte del producto, pero esta documentación no incluye ejemplos reales.
