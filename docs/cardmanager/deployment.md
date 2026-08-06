# Despliegue de CardManager

## Artefactos existentes

`Dockerfile` usa Python 3.12 slim, instala dependencias de WeasyPrint/PostgreSQL, ejecuta `docker-entrypoint.sh` y Gunicorn con tres workers. `docker-compose.yml` define web, PostgreSQL 16, volumen de base y healthchecks; WhiteNoise sirve estáticos.

## Secuencia recomendada

1. Provisionar PostgreSQL y secretos fuera del repositorio.
2. Configurar HTTPS/proxy y hosts/orígenes permitidos.
3. Validar llaves independientes de cifrado y fingerprint.
4. Configurar correo y destinatarios autorizados.
5. Ejecutar `python manage.py check --deploy`, migraciones y `collectstatic`.
6. Crear usuarios/perfiles por canal administrativo seguro.
7. Cargar festivos y política; verificar cadena.
8. Ejecutar smoke test de login/MFA/roles/revelado/reportes sin datos reales.

## Estado

El repositorio está preparado para despliegue, pero esta documentación no confirma Dokploy, DNS, certificados, secretos, backups, workers programados ni entrega de correo del ambiente real. `migrate` y `collectstatic` del entrypoint deben revisarse operativamente antes de cada release.

## Rollback

Restaurar imagen anterior compatible y base desde backup verificado. No revertir migraciones ni rotar llaves sin un plan de descifrado. El procedimiento concreto depende de infraestructura y debe aprobarlo A&S.
