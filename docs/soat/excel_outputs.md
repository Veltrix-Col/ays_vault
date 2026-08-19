# Salidas Excel de SOAT

## Libros generados

Los dos archivos comparten el mismo `<fecha-hora>`:

- `Informe_SOAT_A&S_<fecha-hora>.xlsx`
- `Soporte_SOAT_A&S_<fecha-hora>.xlsx`

### Informe principal

1. `Formato informe`: resultado consolidado, una fila por placa.

### Soporte del procesamiento

1. `Trazabilidad`: candidatos, selección y criterio.
2. `SOAT seleccionados`: filas ganadoras SOAT.
3. `Movilidad seleccionada`: filas ganadoras Movilidad.
4. `Fuente Zoho`: fuente procesada normalizada.

## Formato

Panel congelado en A2, autofiltro, cuadrícula oculta, encabezado azul oscuro con texto blanco/negrita/centrado, ajuste de texto, altura de encabezado 42, anchos por tipo y fechas `dd/mm/yyyy`. `ID CARGA` se destaca en azul claro; criterios 6/7 usan alerta visual y múltiples candidatos se resaltan en trazabilidad.

## Seguridad

Antes de exportar, textos que empiezan por `=`, `+`, `-` o `@` reciben apóstrofo. Se eliminan hipervínculos de todas las celdas. La respuesta añade `no-store`, `no-cache` y `nosniff`.

## Validaciones

Ambos libros no están vacíos y contienen exactamente las hojas esperadas, sin placas duplicadas, universo fuente/final 1:1, selección única por placa e ID/criterios coherentes. El proceso falla antes de descargar si no puede garantizar integridad.
