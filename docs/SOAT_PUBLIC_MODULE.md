# Gestión SOAT pública

## Alcance

Gestión SOAT está disponible en `/soat/` desde el Portal de Aplicaciones. Es pública e independiente de A&S Vault: no autentica usuarios, no crea sesiones, no consulta roles, no usa modelos y no escribe eventos en la cadena de auditoría de Vault.

## Flujo

1. El usuario descarga el reporte desde el enlace Zoho configurado en `SOAT_ZOHO_REPORT_URL`.
2. Conserva el nombre exacto `SOAT_prueba_4.xlsx`.
3. Carga el archivo en `/soat/`.
4. El backend valida nombre, extensión, estructura ZIP/XLSX, tipos internos, tamaño, número de filas y columnas.
5. El procesamiento ejecuta las reglas del script operativo entregado: normalización, enriquecimiento de cancelación, separación SOAT/Movilidad, selección independiente por placa, siete criterios e ID de carga.
6. El navegador recibe el resumen y conserva el archivo generado como `Blob`; el botón de descarga no vuelve a procesar.

El resultado contiene exactamente: `Formato informe`, `Trazabilidad`, `SOAT seleccionados`, `Movilidad seleccionada` y `Fuente Zoho`.

## Referencia privada

`SOAT_REFERENCE_FILE` debe apuntar a `SOAT_prueba_3_Def.xlsx`, por defecto `private_assets/soat/SOAT_prueba_3_Def.xlsx`. El archivo no se incluye en el repositorio, no debe ubicarse en `static/`, `media/` ni una ruta pública, y debe entregarse por un canal administrativo seguro.

## Seguridad y temporales

Cada solicitud usa un directorio temporal aislado; no existe un nombre compartido entre usuarios. La limpieza ocurre al salir del contexto, incluso ante error. El archivo de salida se devuelve en memoria, sin persistencia en base de datos ni publicación en `media`. Se neutralizan celdas que empiezan por `=`, `+`, `-` o `@`, se eliminan hipervínculos y se rechazan macros, ejecutables, DLL, rutas ZIP inseguras o expansiones desproporcionadas.

Variables:

- `SOAT_ZOHO_REPORT_URL`
- `SOAT_REFERENCE_FILE`
- `SOAT_MAX_UPLOAD_BYTES=26214400`
- `SOAT_MAX_ROWS=100000`
- `SOAT_MAX_COLUMNS=200`

## Operación y límites

La aplicación no descarga automáticamente desde Zoho. El administrador debe provisionar la referencia antes de habilitar el flujo real. Un error funcional se presenta en español con HTTP 422; un fallo inesperado usa un mensaje genérico y no expone rutas. SOAT no envía correos.

Validación recomendada:

```powershell
python manage.py check
python manage.py test soat
python manage.py test
```

Antes de datos reales deben validarse el archivo de referencia oficial, equivalencia con una muestra aprobada, límites bajo carga y permisos del directorio temporal del servidor.
