# Ejecución de SOAT

## Web

```powershell
python manage.py runserver 127.0.0.1:8000
```

Abra `/soat/`, cargue un `.xlsx` compatible y descargue el resultado. No se exige un nombre específico de archivo.

## Standalone

El motor `soat/services/legacy_processor.py` contiene un `main()` con opciones `--entrada`, `--referencia`, `--salida`, `--hoja`, `--hoja-movilidad-referencia`, `--base-dir` y `--verbose`. Consulte su `--help` desde un entorno controlado antes de usarlo. Puede seleccionar automáticamente el Excel más reciente cuando no recibe rutas, comportamiento que no existe en la web.

El modo standalone puede usar una referencia histórica; web no. No ejecute ambos modos sobre la misma carpeta sin rutas explícitas.

## Errores

Validaciones funcionales retornan HTTP 422 con mensaje seguro; errores inesperados retornan 500 genérico y se registran. Conserve el archivo fuente original sin modificar y reintente en un directorio limpio.
