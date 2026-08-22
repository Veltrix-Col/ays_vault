# Configuración de SOAT

| Variable | Predeterminado | Uso |
|---|---:|---|
| `SOAT_MAX_UPLOAD_BYTES` | 25 MiB | tamaño máximo del XLSX |
| `SOAT_MAX_ROWS` | 100000 | límite de filas declarado por hoja |
| `SOAT_MAX_COLUMNS` | 200 | límite de columnas declarado por hoja |
| `SOAT_ZOHO_REPORT_URL` | vacío | enlace HTTP(S) opcional al reporte fuente; no se consulta |
| `SOAT_APP_URL` | vacío | enlace externo del catálogo del portal, si se configura |
| `TOOLS_ACCESS_MODE` | `local_public` en desarrollo | frontera global pública/intranet |

`SOAT_ZOHO_REPORT_URL` y `SOAT_APP_URL` se aceptan solo como URLs HTTP(S) seguras por las vistas correspondientes. No incluya credenciales, tokens ni parámetros sensibles.

SOAT comparte settings/servidor con el proyecto, pero no usa base de datos, correo, OAuth ni perfiles Zoho. Los límites deben dimensionarse considerando que el procesamiento ocurre dentro del worker web y carga DataFrames/libros en memoria.
