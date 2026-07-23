# Configuración de correo

A&S Vault centraliza alertas y pruebas administrativas en `vault.notifications`. El proveedor se selecciona con `ALERT_EMAIL_BACKEND`; cambiarlo no requiere modificar código. Los mensajes nunca deben contener PAN, vencimiento, empresa protegida, OTP, códigos de recuperación, contraseñas, tokens ni secretos.

## Desarrollo local

```env
APP_ENV=development
DEBUG=True
EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend
ALERT_EMAIL_BACKEND=console
DEFAULT_FROM_EMAIL=alertas@ays.com.co
ALERT_EMAIL_FROM=alertas@ays.com.co
```

La consola no realiza envíos externos. Las pruebas automatizadas usan `locmem` o mocks y nunca deben configurarse con credenciales reales.

## SMTP Microsoft 365 con contraseña de aplicación

```env
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
ALERT_EMAIL_BACKEND=smtp
EMAIL_HOST=smtp.office365.com
EMAIL_PORT=587
EMAIL_USE_TLS=True
EMAIL_USE_SSL=False
EMAIL_HOST_USER=alertas@ays.com.co
EMAIL_HOST_PASSWORD=<CONTRASEÑA_DE_APLICACIÓN>
DEFAULT_FROM_EMAIL=alertas@ays.com.co
ALERT_EMAIL_FROM=alertas@ays.com.co
EMAIL_TIMEOUT_SECONDS=10
EMAIL_MAX_RETRIES=3
```

`EMAIL_HOST_PASSWORD` debe contener una contraseña de aplicación, no la contraseña normal del buzón. El archivo `.env` no se sube a Git. El secreto debe entregarse mediante un canal seguro aprobado por A&S y su proveedor; no se solicita ni comparte por correo o chat.

Antes de habilitar SMTP se debe solicitar al proveedor:

1. Un buzón institucional remitente autorizado y con capacidad de envío.
2. Confirmación de que MFA está habilitado.
3. Confirmación de que el tenant permite generar contraseñas de aplicación.
4. Entrega de la contraseña de aplicación mediante un canal seguro.
5. Confirmación de que SMTP autenticado está permitido para ese buzón.
6. Una prueba controlada de envío y recepción.

Algunos tenants deshabilitan las contraseñas de aplicación o SMTP autenticado mediante Security Defaults, políticas de autenticación o configuración del buzón. En ese caso no se debe debilitar el tenant: use Microsoft Graph.

## Microsoft Graph

Graph permanece disponible con:

```env
ALERT_EMAIL_BACKEND=graph
MS_GRAPH_TENANT_ID=
MS_GRAPH_CLIENT_ID=
MS_GRAPH_CLIENT_SECRET=
MS_GRAPH_SENDER=alertas@ays.com.co
```

Requiere credenciales de aplicación, consentimiento administrativo `Mail.Send` y restricción del buzón accesible. `MS_GRAPH_CLIENT_SECRET` no se reutiliza como contraseña SMTP.

## Validación y prueba controlada

1. Configure las variables en el gestor seguro del ambiente.
2. Ejecute `python manage.py check`.
3. Reinicie controladamente la aplicación.
4. Ingrese como Administrador.
5. Abra **Correo y destinatarios**.
6. Confirme backend, remitente y estado.
7. Ejecute **Enviar correo de prueba** hacia una dirección corporativa autorizada.
8. Confirme recepción y remitente.
9. Revise el historial y la cadena de auditoría.
10. Confirme que interfaz y logs no muestran secretos.

La prueba usa POST, CSRF, control de rol e idempotencia diaria por administrador/destinatario. Líder y Analista no tienen acceso.

## Timeout, reintentos y errores

`EMAIL_TIMEOUT_SECONDS` limita cada conexión. `EMAIL_MAX_RETRIES` limita los intentos de un envío. Los errores transitorios de conexión, timeout, HTTP 429 o servidor pueden reintentarse; una autenticación SMTP inválida, un destinatario rechazado o un fallo de credenciales Graph finalizan con un código seguro. El historial conserva destinatario enmascarado, proveedor, intentos, resultado y código, no cuerpos ni credenciales.

## Rotación de la contraseña de aplicación

1. Genere una contraseña de aplicación nueva.
2. Actualice `EMAIL_HOST_PASSWORD` en el gestor seguro del ambiente.
3. Reinicie controladamente la aplicación.
4. Ejecute la prueba administrativa.
5. Confirme recepción y auditoría.
6. Revoque la contraseña anterior.

No registre ni conserve la contraseña antigua o nueva en tickets, documentación, logs o base de datos.
