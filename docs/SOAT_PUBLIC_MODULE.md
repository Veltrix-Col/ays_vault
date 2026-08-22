# Gestión SOAT pública

> Documento histórico consolidado. La referencia vigente está en [`soat/README.md`](soat/README.md) y [`soat/technical_handover.md`](soat/technical_handover.md). El código actual aplica **nueve** criterios de gestión, no siete.

## Alcance

Gestión SOAT está disponible en `/soat/` desde el Portal de Aplicaciones. Es pública e independiente de A&S Vault: no autentica usuarios, no crea sesiones, no consulta roles, no usa modelos y no escribe eventos en la cadena de auditoría de Vault.

## Flujo

1. El usuario descarga el reporte desde el enlace Zoho configurado en `SOAT_ZOHO_REPORT_URL`.
2. Carga el archivo en `/soat/`, con cualquier nombre.
3. El backend valida extensión, estructura ZIP/XLSX, tipos internos, tamaño, número de filas y columnas, y que exista una fila de encabezados con las columnas `Placa` y `Ramo (Póliza)` (estructura mínima de un reporte SOAT de Zoho).
4. El procesamiento ejecuta las reglas del script operativo entregado: normalización, separación SOAT/Movilidad, selección independiente por placa, siete criterios e ID de carga. El campo `Motivo cancelación` se usa tal como viene en el archivo subido, sin enriquecimiento externo.
5. El navegador recibe el resumen y conserva el archivo generado como `Blob`; el botón de descarga no vuelve a procesar.

El resultado contiene exactamente: `Formato informe`, `Trazabilidad`, `SOAT seleccionados`, `Movilidad seleccionada` y `Fuente Zoho`.

## Seguridad y temporales

Cada solicitud usa un directorio temporal aislado; no existe un nombre compartido entre usuarios. La limpieza ocurre al salir del contexto, incluso ante error. El archivo de salida se devuelve en memoria, sin persistencia en base de datos ni publicación en `media`. Se neutralizan celdas que empiezan por `=`, `+`, `-` o `@`, se eliminan hipervínculos y se rechazan macros, ejecutables, DLL, rutas ZIP inseguras o expansiones desproporcionadas.

Variables:

- `SOAT_ZOHO_REPORT_URL`
- `SOAT_MAX_UPLOAD_BYTES=26214400`
- `SOAT_MAX_ROWS=100000`
- `SOAT_MAX_COLUMNS=200`

## Operación y límites

La aplicación no descarga automáticamente desde Zoho. Un error funcional se presenta en español con HTTP 422; un fallo inesperado usa un mensaje genérico y no expone rutas. SOAT no envía correos.

Validación recomendada:

```powershell
python manage.py check
python manage.py test soat
python manage.py test
```

Antes de datos reales debe validarse la equivalencia con una muestra aprobada, límites bajo carga y permisos del directorio temporal del servidor.
