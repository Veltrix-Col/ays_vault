#!/bin/sh
set -e

# El contenedor arranca como root para poder corregir el dueño de los
# volúmenes montados (quedan en root la primera vez que Docker los crea).
# Una vez corregido, se baja a appuser con gosu y se re-ejecuta este mismo
# script como usuario sin privilegios para el resto del arranque.
if [ "$(id -u)" = "0" ]; then
    echo "==> Corrigiendo dueño de volúmenes montados..."
    mkdir -p /app/media /app/private_assets
    chown -R appuser:appuser /app/media /app/private_assets
    exec gosu appuser "$0" "$@"
fi

echo "==> Aplicando migraciones..."
python manage.py migrate --noinput

echo "==> Recolectando archivos estáticos (whitenoise)..."
python manage.py collectstatic --noinput

echo "==> Arrancando aplicación..."
exec "$@"
