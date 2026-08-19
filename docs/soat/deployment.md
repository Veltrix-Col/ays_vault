# Despliegue de SOAT

SOAT se despliega con la misma aplicación Django, Dockerfile, Gunicorn y WhiteNoise del proyecto. No tiene servicio, base, volumen ni migración propios.

## Requisitos

- Dependencias pandas/openpyxl instaladas.
- Directorio temporal del sistema escribible y con espacio suficiente.
- Límites de carga coordinados entre proxy, Django y `SOAT_MAX_UPLOAD_BYTES`.
- Worker con memoria/timeout adecuados al máximo admitido.
- `TOOLS_ACCESS_MODE=trusted_intranet` o control equivalente si la URL no debe ser pública.

## Smoke test

1. `python manage.py check`.
2. GET `/soat/` devuelve 200.
3. XLSX sintético compatible produce los dos libros XLSX esperados.
4. Archivo corrupto/macro/traversal es rechazado.
5. No quedan archivos fuente/salida en disco temporal.

No se ha confirmado en esta intervención la configuración real de proxy, límites, almacenamiento temporal ni monitoreo de producción.
