# Arquitectura técnica de SOAT

```text
Navegador
 → `soat.views.upload`
 → `SoatUploadForm`
 → `services.processor.process_soat`
 → `services.legacy_processor` (reglas/tablas)
 → pandas + openpyxl
 → XLSX en memoria → respuesta HTTP
```

## Componentes

- `soat/urls.py`: ruta `upload`.
- `soat/views.py`: GET/POST, mensajes y descarga segura.
- `soat/forms.py`: archivo, ZIP, límites y estructura.
- `soat/services/processor.py`: temporales, orquestación web, saneamiento y resumen.
- `soat/services/legacy_processor.py`: selección, derivación, validación, formato y CLI standalone.
- `templates/soat/upload.html`, `static/css/soat.css`, `static/js/soat.js`: interfaz.
- `soat/tests.py`: regresión.

## Persistencia e integraciones

No hay modelos ni caché SOAT. `TemporaryDirectory` aísla cada ejecución y el resultado final es `bytes`. `SOAT_ZOHO_REPORT_URL` muestra un enlace HTTP(S) opcional; no descarga ni autentica contra Zoho. La vista no crea eventos Vault.

## Dependencias

Django, pandas, openpyxl y el módulo estándar ZIP/temporales. La ejecución ocurre en el proceso web; no existe worker/cola dedicada.
