#!/bin/sh
set -e

echo "==> Aplicando migraciones..."
python manage.py migrate --noinput

echo "==> Recolectando archivos estáticos (whitenoise)..."
python manage.py collectstatic --noinput

echo "==> Arrancando aplicación..."
exec "$@"
