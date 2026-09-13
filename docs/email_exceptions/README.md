# Excepciones de Correo

## Source of Truth

La única fuente de verdad es el flujo `POST inbound → InboundEmail → classifier → EmailException`. Local, staging y production ejecutan el mismo classifier. La aplicación nunca abre, importa ni carga automáticamente Excel, `v5.py`, `report_v5.py` u otros archivos de `docs/email_exceptions/reference/`; esos archivos son solo contexto documental.

## Environment Variables

| Variable | Tipo | Local | Staging | Production | Default seguro | Recomendado Production |
|---|---|---:|---:|---:|---|---|
| `EMAIL_EXCEPTIONS_ENABLED` | FEATURE FLAG | No | Sí controlado | Sí controlado | `false` | `true` |
| `EMAIL_EXCEPTIONS_INBOUND_ENABLED` | FEATURE FLAG | No | Sí controlado | Sí controlado | `false` | `true` |
| `EMAIL_EXCEPTIONS_INBOUND_TOKEN` | SECRET | No | Sí | Sí | vacío | secreto del gestor de secretos |
| `EMAIL_EXCEPTIONS_ALLOWED_MAILBOXES` | CONFIG | No | Sí | Sí | ambos buzones | lista explícita aprobada |
| `EMAIL_EXCEPTIONS_MAX_PAYLOAD_BYTES` | CONFIG | Sí | Sí | Sí | 5 MiB | 5 MiB o menor |
| `EMAIL_EXCEPTIONS_MAX_ATTACHMENT_BYTES` | CONFIG | Sí | Sí | Sí | 10 MiB | según storage aprobado |
| `EMAIL_EXCEPTIONS_ZOHO_TASK_WRITE_ENABLED` | SAFETY GUARD | `false` | `false` | `false` | `false` | `false` inicialmente |
| `ZOHO_PRODUCTION_WRITE_ENABLED` | SAFETY GUARD existente | según módulos | valor Production existente | valor Production existente | no cambiar | no modificar por Excepciones |

No se agrega un segundo master switch de Zoho ni configuración de Outlook/Graph. Production Task WRITE requerirá ambos guards global y específico, además de los guards/confirmación del publisher existente.

## Inyección mediante Dokploy/Compose

Dokploy debe definir las variables en el entorno de la aplicación. `docker-compose.yml` las pasa explícitamente al servicio `web`; una variable configurada en Dokploy pero ausente del bloque `environment` no llega a Django.

Crear manualmente en Dokploy:

| Variable | Valor inicial Production | Tipo |
|---|---|---|
| `EMAIL_EXCEPTIONS_ENABLED` | `true` | configuración |
| `EMAIL_EXCEPTIONS_INBOUND_ENABLED` | `true` | configuración |
| `EMAIL_EXCEPTIONS_INBOUND_TOKEN` | token nuevo, exclusivo de este deployment | secreto |
| `EMAIL_EXCEPTIONS_ALLOWED_MAILBOXES` | `comunicaciones@segurosays.com,aysltda@asesorsura.com` | configuración |
| `EMAIL_EXCEPTIONS_MAX_PAYLOAD_BYTES` | `5242880` | configuración |
| `EMAIL_EXCEPTIONS_MAX_ATTACHMENT_BYTES` | `10485760` | configuración |
| `EMAIL_EXCEPTIONS_ZOHO_TASK_WRITE_ENABLED` | `false` | guard de seguridad |

No cambiar en Dokploy los valores existentes de Zoho Production (`ZOHO_ACTIVE_PROFILE`, `ZOHO_PRODUCTION_ENABLED`, `ZOHO_PRODUCTION_WRITE_ENABLED`) por causa de este módulo. El token inbound debe generarse y almacenarse como secreto independiente; no se debe copiar ningún secreto de Zoho ni de otro deployment.

Después de guardar las variables, validar dentro del contenedor `web` sin imprimir secretos:

```bash
docker compose config --quiet
docker compose exec web python manage.py check --deploy
docker compose exec web python manage.py migrate --plan
```

La comprobación de presencia debe reportar únicamente nombres y flags saneados; nunca imprimir el valor de `EMAIL_EXCEPTIONS_INBOUND_TOKEN`.

## Local, Staging y Production

Local debe probar con fixtures creados como `InboundEmail`, no con históricos. Staging debe usar HTTPS, secreto gestionado y un buzón de prueba permitido. Production exige fallo de arranque si inbound está habilitado sin token o allowlist, PostgreSQL, storage privado y logging sin secretos. Los flags peligrosos permanecen apagados en `.env.example`.

## Power Automate Authentication

Power Automate envía `X-Email-Exceptions-Token` mediante una conexión/secret seguro, nunca como valor visible en el flujo. Django usa comparación constant-time (`hmac.compare_digest`). Token ausente o inválido devuelve `401` sin revelar detalles. El token se rota generando uno nuevo, desplegándolo primero en el servicio y actualizando después la conexión; durante una rotación coordinada puede aceptarse temporalmente un segundo secreto solo mediante un cambio explícito de código/configuración, no por fallback silencioso.

El buzón debe pertenecer a `EMAIL_EXCEPTIONS_ALLOWED_MAILBOXES`; si no, se devuelve `403`. El payload duplicado por `source_mailbox + message_id` devuelve `200` con los IDs existentes. Content-type inválido devuelve `415`, JSON inválido o campos requeridos ausentes `400`, payload demasiado grande `413` y adjunto inválido `500` actualmente; el correo no se crea parcialmente.

## Secret Rotation

No registrar tokens, cuerpos completos ni adjuntos en logs. Rotar fuera de horario, probar un POST válido y un token antiguo inválido, y verificar que los reintentos siguen siendo idempotentes.

## Zoho Safety Guards

La app no busca ni actualiza Tasks existentes. La escritura futura debe comprobar `ZOHO_PRODUCTION_WRITE_ENABLED`, `EMAIL_EXCEPTIONS_ZOHO_TASK_WRITE_ENABLED`, el perfil activo y la confirmación específica del publisher existente. Ningún default activa WRITE.

## Failure Modes

La persistencia es transaccional. Si falla validación o clasificación, el endpoint responde error y no deja una excepción huérfana. Si falla Zoho, la excepción debe conservarse y quedar auditable como fallo técnico; no se reintenta automáticamente una respuesta incierta.

## Production Checklist

- [ ] Migraciones aplicadas y respaldadas.
- [ ] Variables configuradas desde gestor seguro.
- [ ] Token inbound generado y probado.
- [ ] Allowlist de buzones aprobada.
- [ ] HTTPS, `DEBUG=false`, hosts y CSRF correctos.
- [ ] Storage privado y permisos de filesystem verificados.
- [ ] Límites de payload/adjuntos definidos.
- [ ] Logging seguro, backups y PostgreSQL validados.
- [ ] Endpoint válido, inválido, duplicado y retry probados.
- [ ] `COMUNICACIONES_V1` activa; SURA `REVIEW_ONLY`.
- [ ] `EMAIL_EXCEPTIONS_ZOHO_TASK_WRITE_ENABLED=false`.
- [ ] Los guards globales de Zoho Production permanecen con los valores operativos existentes.
- [ ] Task probada primero en Sandbox controlado.
- [ ] Auditoría, RBAC y anti-IDOR validados.

## Objetivo

Recibir correos de buzones configurados, conservarlos para auditoría, clasificarlos con políticas versionadas y presentar únicamente los que requieren revisión humana. La primera versión permite ignorar una gestión y deja preparada la creación de una Task Zoho detrás de los guards existentes.

## Arquitectura

Power Automate transporta el JSON a `POST /operaciones/excepciones-correo/api/inbound/`. Django valida el token, persiste `InboundEmail`, clasifica mediante `email_exceptions.services.classify_inbound_email`, crea `EmailException` cuando corresponde y registra `EmailAuditEvent`. La unicidad `source_mailbox + external_message_id` hace seguro el reintento.

## Payload

```json
{"source_mailbox":"comunicaciones@segurosays.com","message_id":"id-externo","conversation_id":"conv-1","received_at":"2026-09-12T10:32:00-05:00","from":{"name":"Remitente","email":"persona@dominio.com"},"to":[],"cc":[],"subject":"Comprobante de pago","body_text":"...","attachments":[]}
```

Enviar `Content-Type: application/json` y `X-Email-Exceptions-Token`. Configurar el secreto como `EMAIL_EXCEPTIONS_INBOUND_TOKEN`; nunca registrarlo en logs ni incluirlo en Power Automate como texto visible compartido.

## Estados y políticas

Los estados son `PENDING`, `MANAGED`, `IGNORED` y `ERROR`. `COMUNICACIONES_V1` está activa para `comunicaciones@segurosays.com`. `SURA_V1` queda `REVIEW_ONLY` para `aysltda@asesorsura.com` hasta definir sus reglas. El ruido se registra como inbound sin crear excepción.

## Adjuntos y seguridad

El modelo `EmailAttachment` usa almacenamiento privado, conserva checksum y distingue inline/documental. El endpoint limita el cuerpo con `EMAIL_EXCEPTIONS_MAX_PAYLOAD_BYTES`; la carga binaria, MIME permitido, sanitización y descarga autenticada deben completarse al conectar el transporte de adjuntos.

## Auditoría y Zoho

Se registran recepción/clasificación implícitamente en el ingreso y `EXCEPTION_CREATED`, `EMAIL_LINKED`, `IGNORED` y `TASK_CREATE_REQUESTED`. No se consultan Tasks históricas ni se actualizan Tasks existentes. La creación efectiva queda bloqueada hasta habilitar Zoho Sandbox y su confirmación explícita; `ZOHO_PRODUCTION_WRITE_ENABLED` no se modifica y permanece falso.

## Extensión y troubleshooting

Para agregar un buzón, añadir su perfil en `services.py` y sembrar `EmailPolicyProfile`. Para agregar una regla, incluirla en `classify_inbound_email` con identificador, versión, explicación y pruebas. Activar SURA requiere reemplazar `REVIEW_ONLY` por una política validada y pruebas de regresión. Si Power Automate recibe `401`, revisar el header/token; `415` indica content-type incorrecto; `400` payload incompleto; `200` significa reintento idempotente.
