# Informes y exportaciones seguras

> Documento histórico conservado como antecedente. La referencia vigente es [`cardmanager/reports_and_exports.md`](cardmanager/reports_and_exports.md).

## Alcance implementado

El Centro de Informes es exclusivo del Administrador y ofrece seis informes iniciales. Líder y Analista no pueden abrir el catálogo, llamar endpoints de exportación ni descargar archivos:

- Línea de Tiempo y Alertas: trazabilidad administrativa sin valores protegidos.
- Accesos: eventos de ingreso, MFA, dispositivo, horario y reemplazo de sesion autorizados.
- Adopción: resume uso sin rankings competitivos.
- Tarjetas Seguras: ID interno, cliente/alias autorizado, franquicia, últimos cuatro y estado. Se excluyen Empresa, PAN y vencimiento.
- Salud Operativa.

No se implementaron aun informes dedicados de auditoria pura, sesiones, dispositivos, usuarios, politicas, excepciones, festivos o notificaciones. Su incorporacion futura debe reutilizar la misma matriz de roles y el registro de exportaciones.

## Filtros

La Línea de Tiempo valida fechas, rango máximo, usuario, rol, evento, severidad, resultado, horario, dispositivo, IP, ID/últimos cuatro, alerta y criterios avanzados. No existe búsqueda libre sobre metadatos o campos cifrados.

Los accesos rapidos permanecen en parametros GET. Los chips permiten quitar filtros individualmente. Las exportaciones copian los criterios a un formulario POST protegido con CSRF y vuelven a validarlos en backend.

## Excel

`openpyxl==3.1.5` produce un `.xlsx` editable con hojas `Resumen` y `Datos`, encabezados corporativos, autofiltro, panel congelado, tabla y anchos acotados. No usa macros. Antes de escribir una celda se antepone una comilla simple a cadenas que empiezan por `=`, `+`, `-` o `@`; asi ningun dato de usuario se interpreta como formula.

## PDF

`WeasyPrint==69.0` genera PDF A4 en memoria con orientación automática o seleccionada, logo, metadatos, filtros, tabla, advertencia y numeración de páginas. La configuración se abre en un modal reutilizable; los botones muestran estado de carga y bloquean envíos dobles. La tabla usa disposición fija y evita reglas de salto por fila costosas. No usa ReportLab ni deja temporales. En Windows pueden aparecer avisos GLib del sistema que no invalidan el PDF.

En el entorno Windows/OneDrive de QA, el módulo `brotli.py` instalado era un marcador sin contenido local y provocaba `PermissionError` durante la importación de WeasyPrint. Se fijó `brotlicffi==1.2.0.0`, sin cambiar el motor PDF. La validación real posterior generó 295 registros, 46 páginas y 180.829 bytes con firma `%PDF`; el endpoint registró 8.937 ms. El primer uso de un proceso puede sumar el costo de inicialización de WeasyPrint.

## Seguridad, auditoria y limites

Solo se genera por POST autenticado. Los endpoints verifican perfil activo y rol; no confian en IDs o tipos enviados por la interfaz. Los valores predeterminados son:

- `REPORT_XLSX_MAX_ROWS=5000`
- `REPORT_PDF_MAX_ROWS=1000`
- `REPORT_DEFAULT_MAX_DAYS=90`
- `REPORT_LARGE_EXPORT_ALERT_THRESHOLD=1000`

La consulta se corta en limite + 1 para detectar exceso sin materializar un conjunto ilimitado. Si excede, se rechaza y se solicita reducir filtros. Cada intento crea `ReportExport` con filtros ya traducidos y no sensibles; cada resultado genera `REPORT_EXPORT` dentro de la cadena hash. Un volumen igual o mayor al umbral genera `LARGE_REPORT_EXPORT`.

Las respuestas incluyen `Cache-Control: no-store`, `Pragma: no-cache` y `X-Content-Type-Options: nosniff`. El nombre se construye internamente, se sanea y se transmite con codificacion segura; no intervienen rutas proporcionadas por el usuario. Los archivos no se guardan en `media` ni en base de datos.

## Procedimiento de validacion

1. Probar cada rol y confirmar las cards disponibles.
2. Generar Excel, abrirlo con openpyxl y revisar hojas, autofiltro, panel congelado y ausencia de formulas.
3. Generar PDF y confirmar firma `%PDF`, encabezado, filtros y tabla.
4. Buscar explicitamente PAN de prueba, vencimiento, secretos y session keys en ambos resultados.
5. Forzar un limite bajo y confirmar rechazo, historial y auditoria integra.
6. Ejecutar `python manage.py verify_audit_chain` y la suite completa.

## Riesgos pendientes

Antes de datos reales siguen siendo obligatorios el QA en infraestructura productiva, pruebas PostgreSQL y de concurrencia, pentest, KMS/Key Vault, SIEM inmutable, backups cifrados restaurados y revision formal de cumplimiento. WeasyPrint debe instalarse y probarse en el sistema operativo definitivo. No usar datos reales todavia.
