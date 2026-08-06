# Alertas y correo Microsoft 365

> Documento histórico conservado como antecedente. La referencia vigente es [`cardmanager/audit_and_monitoring.md`](cardmanager/audit_and_monitoring.md) y [`cardmanager/configuration.md`](cardmanager/configuration.md).

## Centro de Alertas

Cada alerta contiene tipo, severidad, estado, actor/afectado, IP, dispositivo, politica, excepcion, descripcion, evidencia segura, recomendacion, vencimiento, asignacion y cierre. Estados: nueva, en revision, revisada heredada, justificada, escalada, cerrada y reabierta.

Cerrar o justificar exige comentario. Escalar exige destinatario o grupo. Asignar y toda transicion crean `AlertTransition` y un `AuditEvent`. No hay endpoint de borrado y Django Admin es de solo lectura.

## Inactividad y adopcion

`evaluate_security_policies` evalua falta de ingreso/operacion, usuarios sin MFA, excepciones/dispositivos proximos a vencer, alertas vencidas e integridad. Las claves SHA-256 de idempotencia evitan repetir la misma alerta del periodo. `--dry-run` registra ejecucion pero no crea alertas ni cambia estados.

Ejecucion sugerida en Task Scheduler o cron:

```powershell
python manage.py evaluate_security_policies
```

La funcion `build_periodic_summary(days=1|7)` prepara agregados diarios/semanales sin datos sensibles. El envio automatico queda deshabilitado hasta que exista configuracion explicita.

## Backends

- `console`: usa exclusivamente consola o `locmem`; recomendado en desarrollo y pruebas.
- `smtp`: usa el backend SMTP de Django con TLS, timeout y credenciales tomadas del entorno. Para Microsoft 365 se espera `smtp.office365.com:587`, usuario completo y contraseña de aplicación.
- `graph`: conserva MSAL `ConfidentialClientApplication`, OAuth 2.0 client credentials y `POST /v1.0/users/{sender}/sendMail`. `microsoft_graph` se acepta temporalmente como alias compatible.

Variables: `ALERT_EMAIL_BACKEND`, `EMAIL_BACKEND`, `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_USE_TLS`, `EMAIL_USE_SSL`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `DEFAULT_FROM_EMAIL`, `ALERT_EMAIL_FROM`, `ALERT_EMAIL_ADMIN`, `ALERT_EMAIL_LEADER`, `MS_GRAPH_TENANT_ID`, `MS_GRAPH_CLIENT_ID`, `MS_GRAPH_CLIENT_SECRET`, `MS_GRAPH_SENDER`, `EMAIL_TIMEOUT_SECONDS`, `EMAIL_MAX_RETRIES` y `VAULT_BASE_URL`.

Los secretos viven solo en variables de entorno y nunca se escriben en logs/base de datos. SMTP usa una contraseña de aplicación, nunca la contraseña normal del buzón; solo funcionará si el tenant y el buzón permiten ese método. Graph requiere registro de aplicación, consentimiento administrativo para `Mail.Send` y una política que limite el buzón accesible.

## Entrega, reintentos e idempotencia

Cada alerta/destinatario produce un hash único. Un envío exitoso no se repite. SMTP y Graph clasifican errores seguros: una conexión transitoria puede reintentarse hasta el límite, mientras una autenticación inválida no entra en un bucle. Los fallos no abortan la operación sensible. El Administrador puede reintentar un fallo con `policy_admin` y motivo. Si el destinatario ya no está configurado, el reintento falla de manera segura.

La bitacora conserva destinatario enmascarado/hash, fechas, resultado, codigo seguro, intentos, proximo intento, backend e identificador externo. No conserva cuerpo, PAN, vencimiento, secretos, tokens, credenciales ni codigos MFA.

## Plantillas y tipos

Las plantillas HTML/texto incluyen A&S Vault, tipo, severidad, usuario, fecha/hora, IP, dispositivo resumido, motivo, recomendación y enlace. Los únicos correos automáticos son: inicio de sesión fuera de horario, inicio de sesión en fin de semana, revelado fuera de horario y revelado en fin de semana. Festivos y coincidencias de varias condiciones producen un único mensaje. Copia, creación/edición/desactivación, reautenticación, ventanas, contextos, reportes, SOAT y operaciones administrativas normales conservan auditoría/alerta cuando aplica, pero no generan correo.

## Prueba de produccion simulada

Use primero console/locmem, simule fallo y reintento y confirme idempotencia. Después configure un buzón institucional de prueba y use el botón administrativo **Enviar correo de prueba**, seleccionando uno de los cuatro escenarios ficticios. No configure credenciales reales durante pruebas automatizadas. En Graph, un HTTP 202 confirma aceptación, no entrega final. **No usar datos reales todavía.**
