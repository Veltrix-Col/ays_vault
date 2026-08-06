# Fuentes de datos de SOAT

## Fuente web vigente

Un libro `.xlsx` cargado por el operador, con al menos las columnas `Placa` y `Ramo (Póliza)`. El nombre del archivo es libre. La hoja se resuelve por estructura; la compatibilidad histórica usa `Sheet0` cuando corresponde.

Campos fuente reconocidos por el motor incluyen placa, líder, analista, vendedor, empresa, gestión, fechas, estados, motivo de cancelación, póliza, tomador, nombre, identificación, afiliado, ramo, aseguradora, correos, responsable e ID del registro asegurado. Consulte [field_mapping.md](field_mapping.md).

## Universos

- **SOAT:** filas cuyo ramo normalizado es exactamente `SOAT`.
- **Movilidad:** todas las demás filas. Esta regla amplia requiere validación de A&S cuando el archivo contenga otros ramos.

## Referencia histórica opcional

El CLI en `legacy_processor.py` admite `--referencia` para enriquecer el motivo de cancelación. El flujo web **no** lo usa. `private_assets/soat/README.md` marca esa referencia como obsoleta para web.

## Zoho

El archivo puede ser un reporte exportado de Zoho, pero SOAT no llama APIs Zoho. `SOAT_ZOHO_REPORT_URL` es solo un enlace seguro visible. Los scripts independientes nombrados históricamente no existen como archivos en este repositorio; no se pudo contrastar su implementación.
