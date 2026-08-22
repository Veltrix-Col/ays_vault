# Configuración local de correo de CardManager

> Guía operativa histórica. La matriz vigente de variables y estados está en [`cardmanager/configuration.md`](cardmanager/configuration.md); nunca copie credenciales en esta guía.

CardManager centraliza alertas y pruebas administrativas en `vault.notifications`.
`ALERT_EMAIL_BACKEND` selecciona `console`, `smtp` o `graph`. Los mensajes nunca
deben contener PAN, vencimiento, empresa protegida, OTP, códigos de recuperación,
contraseñas, tokens ni secretos.

Los únicos correos automáticos habilitados siguen siendo los accesos y revelados
fuera del horario definido. Esta guía no cambia esa política.

## Archivo local y secreto

Use `.env` en la raíz del proyecto. Está ignorado por Git y Docker; no copie sus
valores a `.env.example`, Compose, Dockerfile, documentación, tickets o capturas.
`EMAIL_HOST_PASSWORD` debe contener una contraseña de aplicación, no la contraseña
normal del buzón.

```env
APP_ENV=development
DEBUG=True
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
ALERT_EMAIL_BACKEND=smtp
EMAIL_HOST=smtp.office365.com
EMAIL_PORT=587
EMAIL_USE_TLS=True
EMAIL_USE_SSL=False
EMAIL_HOST_USER=
EMAIL_HOST_PASSWORD=
DEFAULT_FROM_EMAIL=
ALERT_EMAIL_FROM=
EMAIL_TIMEOUT_SECONDS=10
EMAIL_MAX_RETRIES=3
```

SMTP solo funcionará si el tenant y el buzón permiten SMTP autenticado y
contraseñas de aplicación. Si Microsoft 365 lo bloquea, no debilite el tenant:
mantenga Microsoft Graph como alternativa.

## A. Prueba con `.venv` en Windows

1. Edite `.env` localmente y configure únicamente un buzón institucional de prueba.
2. Active el entorno y valide sin enviar:

```powershell
Set-Location "C:\ruta\al\proyecto"
.\.venv\Scripts\Activate.ps1
python manage.py check
```

`manage.py check` valida backend, host, puerto, TLS/SSL, remitente, timeout y
presencia de credenciales. Nunca imprime la contraseña. En la interfaz,
**Correo y destinatarios** muestra backend, remitente y estado, sin secretos.

3. Inicie la aplicación:

```powershell
python manage.py runserver
```

4. Ingrese como Administrador, abra **Correo y destinatarios**, seleccione un
   escenario ficticio y envíe únicamente a una dirección corporativa autorizada.
   La acción usa POST, CSRF, control de rol e idempotencia diaria.
5. Confirme asunto con prefijo `[PRUEBA]`, remitente, recepción, historial y
   auditoría. Un error de autenticación se presenta con código seguro y no debe
   producir una página 500.

Prueba alternativa desde Django shell, solo con un destinatario autorizado:

```powershell
python manage.py shell -c "from vault.notifications import send_notification; r=send_notification(notification_type='EMAIL_TEST',recipient='DESTINATARIO_AUTORIZADO',subject='CardManager | Prueba de configuración de correo',text_body='Este es un mensaje de prueba generado desde el entorno local de CardManager. No corresponde a una alerta real.',html_body='<p>Este es un mensaje de prueba generado desde el entorno local de CardManager.</p><p>No corresponde a una alerta real.</p>',idempotency_key='manual-email-test-local'); print(r.result, r.safe_error_code or 'OK')"
```

La salida solo muestra el resultado y un código seguro.

## B. Prueba mediante Docker

El servicio real se llama `web` y usa PostgreSQL. Compose requiere además las
variables de base de datos, cifrado, hosts y URL base indicadas en `.env.example`.
No ejecute Compose con valores vacíos.

```powershell
docker compose --env-file .env config --quiet
docker network inspect dokploy-network
docker compose --env-file .env build
docker compose --env-file .env up
```

Si la red externa `dokploy-network` no existe en el equipo local, créela una sola
vez con `docker network create dokploy-network`. No use `down -v`, `system prune`
ni elimine volúmenes.

En otra terminal:

```powershell
docker compose --env-file .env ps
docker compose --env-file .env logs web
docker compose --env-file .env exec web python manage.py check
```

Después use el mismo botón administrativo de prueba. Para terminar sin borrar
datos: presione `Ctrl+C` si se inició en primer plano o ejecute
`docker compose --env-file .env stop`.

## Volver al backend de consola

Modifique únicamente el `.env` local:

```env
EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend
ALERT_EMAIL_BACKEND=console
EMAIL_HOST_USER=
EMAIL_HOST_PASSWORD=
```

Reinicie la aplicación. La consola no realiza envíos externos.

## Microsoft Graph

Graph permanece disponible con `ALERT_EMAIL_BACKEND=graph` y las variables
`MS_GRAPH_TENANT_ID`, `MS_GRAPH_CLIENT_ID`, `MS_GRAPH_CLIENT_SECRET` y
`MS_GRAPH_SENDER`. El secreto Graph nunca se reutiliza como contraseña SMTP.

## Rotación y limpieza

1. Genere una contraseña de aplicación nueva.
2. Actualice `EMAIL_HOST_PASSWORD` solo en el gestor seguro o `.env` local.
3. Reinicie, ejecute la prueba controlada y confirme recepción.
4. Revoque la contraseña anterior.
5. Para limpiar el entorno local, vuelva a consola y borre el secreto de `.env`;
   no borre bases de datos ni volúmenes.
