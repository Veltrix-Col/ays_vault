param(
    [switch]$Full
)

$ErrorActionPreference = "Stop"
$env:DJANGO_SETTINGS_MODULE = "config.settings_test"
$env:APP_ENV = "test"
$env:DEBUG = "true"
$env:ZOHO_PRODUCTION_WRITE_ENABLED = "false"
$env:ZOHO_ENABLED = "false"

python manage.py check --settings=config.settings_test
python manage.py makemigrations --check --settings=config.settings_test
python manage.py migrate --settings=config.settings_test --noinput
python manage.py collectstatic --noinput --settings=config.settings_test

if ($Full) {
    python manage.py test --settings=config.settings_test
} else {
    python manage.py test cotizacion_colectivos --settings=config.settings_test
}
