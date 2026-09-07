# QA local reproducible

El repositorio declara sus dependencias en `requirements.txt`; las utilidades
de auditoría están en `requirements-dev.txt`.

En PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
$env:DJANGO_SETTINGS_MODULE = "config.settings_test"
python manage.py check --settings=config.settings_test
python manage.py makemigrations --check --settings=config.settings_test
python manage.py migrate --settings=config.settings_test --noinput
python manage.py test cotizacion_colectivos --settings=config.settings_test
python manage.py test --settings=config.settings_test
```

También puede ejecutarse `.\scripts\test.ps1` (o `-Full`). El perfil de
test usa SQLite en memoria, correo `locmem`, un directorio temporal para
`COLECTIVOS_PRIVATE_ROOT` y todos los flags de escritura Zoho en `false`.
`collectstatic` sólo escribe dentro del directorio temporal del perfil.

El módulo de settings de test habilita únicamente el bypass local de acceso
interno (`COLECTIVOS_INTERNAL_PUBLIC_ACCESS`) para que los tests de vistas
existentes puedan usar el cliente anónimo; no cambia los valores de producción
ni habilita ningún WRITE.

Las pruebas normales no usan red ni SMTP. Las integraciones Sandbox deben ser
órdenes separadas, con sus guards y confirmación explícita; nunca forman parte
de `manage.py test`. No se deben configurar tokens, contraseñas ni claves de
Production en `.env.test`.

El SDK `ays-zoho-sdk` se instala desde la referencia Git fijada en
`requirements.txt`; no depende de una ruta local de Windows. Si el repositorio
privado requiere autenticación, configure el acceso Git de forma externa sin
guardar el token en archivos ni imágenes Docker.
