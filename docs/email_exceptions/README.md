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

La app no busca ni actualiza Tasks existentes. La escritura debe comprobar `ZOHO_PRODUCTION_WRITE_ENABLED`, `EMAIL_EXCEPTIONS_ZOHO_TASK_WRITE_ENABLED`, el perfil activo y la confirmación específica del publisher existente. El guard específico de este módulo queda apagado por defecto; no se cambia el valor global de Production que utilizan otros módulos.

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

## Acceso humano y acciones del módulo

La UI pertenece a las herramientas internas generales y reutiliza el acceso
heredado de Intranet aplicado por `TrustedIntranetAccessMiddleware`. En
Production, un contexto SSO validado permite entrar sin `UserProfile` de Vault,
roles Card Manager, grupos manuales ni permisos Django específicos del módulo.
En local, `local_public` permite el acceso de lectura únicamente con
`DEBUG=true` o durante tests, siguiendo el mismo gate general.

El acceso a la herramienta no elimina los controles de las acciones: las
mutaciones siguen siendo POST con CSRF y validaciones funcionales; las acciones
operativas requieren una identidad activa y el contexto/permiso operativo que
corresponda. No se asignan roles de Vault ni se infiere autorización desde
correo, dominio, organización, headers o datos del request. El antiguo comando
de administración de permisos del módulo ya no forma parte del proyecto.

## Payload

```json
{"source_mailbox":"comunicaciones@segurosays.com","message_id":"id-externo","conversation_id":"conv-1","received_at":"2026-09-12T10:32:00-05:00","from":{"name":"Remitente","email":"persona@dominio.com"},"to":[],"cc":[],"subject":"Comprobante de pago","body_text":"...","attachments":[]}
```

Enviar `Content-Type: application/json` y `X-Email-Exceptions-Token`. Configurar el secreto como `EMAIL_EXCEPTIONS_INBOUND_TOKEN`; nunca registrarlo en logs ni incluirlo en Power Automate como texto visible compartido.

## Estados y políticas

Los estados operativos son `PENDING`, `MANAGED`, `IGNORED` y `ERROR`. El estado persistente de clasificación de `InboundEmail` es `PENDING`, `EXCEPTION`, `NO_MATCH` o `ERROR`. Solo un `EXCEPTION` crea un `EmailException`; un `NO_MATCH` conserva el correo recibido, pero no entra en la bandeja operativa. `COMUNICACIONES_V1` está activa para `comunicaciones@segurosays.com` y `SURA_V1` aporta contexto, pero no convierte automáticamente todo correo en excepción.

## Filosofía de clasificación

El clasificador es determinista, explicable y no usa IA. Las señales fuertes —por ejemplo `pago doble`, `pago duplicado`, facturas no recibidas, inconsistencias de aportes o documentación pendiente— pueden producir una excepción sin organización conocida. Las señales contextuales —como comprobante, factura, documentos, cobro o emisión— necesitan evidencia adicional: póliza, periodo, arrendamiento, organización, remitente coherente o una frase accionable. Una palabra genérica aislada termina en `NO_MATCH`.

La organización se infiere desde el remitente, nombre, asunto y cuerpo, incluyendo texto de mensajes reenviados. Sirve para enriquecer confianza y familia, pero no es un gate global para eventos fuertes. Boletines, newsletters, reuniones, respuestas automáticas y avisos meramente informativos se excluyen si no existe una señal operativa concluyente.

Cada excepción conserva `event_type`, `rule_id`, `confidence`, `exception_reason` y las señales coincidentes en `classification_details`. Para agregar una regla, definir primero la señal específica, sus contextos válidos y sus negativos; asignar un identificador estable; añadir casos positivos y negativos a `email_exceptions/tests/test_classification.py`; y verificar que el caso ambiguo continúe en `NO_MATCH`.

## Clasificador universal V6.3.1

El clasificador público único es `classify_inbound_email`. V6.3.1 se usa como primera política universal: el evento se determina por la semántica del mensaje actual y la organización solo enriquece el resultado. El resultado en memoria contiene `message_outcome`, `action_required`, `action_type` y `scope`, además de familia, evento, regla, confidence y motivo. El catálogo `action_type` incluye los diez valores canónicos de V6.3.1, incluido `EXPAND_ATTACHMENT`. No se crean clasificadores por buzón ni reglas universales condicionadas a SURA, BEMSA u otra organización.

La política vigente recupera únicamente eventos accionables verificables: pagos duplicados, facturas no recibidas, comprobantes con contexto, inconsistencias de aportes, complementos o documentación explícitamente requeridos, solicitudes de emisión, cobros con documentación y señales universales V6.3.1 de reembolso rechazado, firma fallida, legalización, corrección y reagendamiento. Una confirmación actual de éxito prevalece sobre señales de fallo; las palabras genéricas aisladas terminan en `NO_MATCH`.

La precedencia es: alcance `BATCH`/`PLATFORM`, éxito actual, fallas explícitas, acción pendiente, ruido y señales contextuales. El texto actual tiene prioridad sobre bloques citados; un éxito actual no crea una excepción aunque el mensaje anterior contenga un error. `BATCH` y `PLATFORM` pueden ser excepciones semánticas con `ACTION_REQUIRED`, pero nunca se convierten en excepciones individuales. La confianza documenta la clasificación, pero nunca sustituye una condición lógica.

Las reglas históricas V4/V5 permanecen como referencia documental y no se cargan en runtime. Solo se absorben señales compatibles con V6.3.1 y con negativos protegidos; candidatos genéricos o reglas `REVIEW` amplias no crean excepciones automáticamente.

## Adjuntos y seguridad

El modelo `EmailAttachment` usa almacenamiento privado, conserva checksum y distingue inline/documental. El endpoint limita el cuerpo con `EMAIL_EXCEPTIONS_MAX_PAYLOAD_BYTES`; la carga binaria, MIME permitido, sanitización y descarga autenticada deben completarse al conectar el transporte de adjuntos.

## Auditoría y Zoho

Se registran recepción/clasificación implícitamente en el ingreso y `EXCEPTION_CREATED`, `EMAIL_LINKED`, `IGNORED` y `TASK_CREATE_REQUESTED`. No se consultan Tasks históricas ni se actualizan Tasks existentes. La creación efectiva queda bloqueada hasta habilitar Zoho Sandbox y su confirmación explícita; `ZOHO_PRODUCTION_WRITE_ENABLED` no se modifica y permanece falso.

## Extensión y troubleshooting

Para agregar un buzón, añadir su perfil en `services.py` y sembrar `EmailPolicyProfile`. Para agregar una regla, incluirla en `classify_inbound_email` con identificador, versión, explicación y pruebas. Activar SURA requiere reemplazar `REVIEW_ONLY` por una política validada y pruebas de regresión. Si Power Automate recibe `401`, revisar el header/token; `415` indica content-type incorrecto; `400` payload incompleto; `200` significa reintento idempotente.

## Casos, correlación y persistencia — Fases C–E.1

El flujo para cada correo nuevo es `InboundEmail` → `classify_inbound_email` (V6.3.1) → retrieval B.2.1 → `correlate_email_to_case` → `ExceptionCase`/`CaseMessage` → `EmailException` cuando la clasificación es elegible. La clasificación es la única autoridad semántica; `correlation.py` es la autoridad de decisión de correlación; `candidate_retrieval.py` recupera casos sin decidir la asociación. `CandidateRetrievalSession` reutiliza un índice en memoria dentro de una ejecución y no mantiene cache global.

El retrieval usa referencias funcionales y `conversation_id`; no descarta candidatos por organización, familia, evento, asunto, remitente, dominio ni buzón. Las referencias conservan la fuerza definida por el motor: póliza o periodo aislados no bastan para `HIGH`. Un identificador fuerte contradictorio bloquea métodos inferiores; múltiples candidatos `HIGH` permanecen ambiguos. La política prefiere un caso separado antes que una fusión dudosa. `STRUCTURED`/`MEDIUM` y `LOW` no hacen auto-link. `BATCH` y `PLATFORM` no originan casos individuales; `SUCCESS` puede asociarse como `RESOLUTION` sin cerrar el caso; `INFORMATIONAL` puede asociarse como `CONTEXT` solo con correlación `HIGH`.

`case_key` se genera canónicamente desde evidencia funcional cuando existe, `conversation_id` cuando corresponde y, de lo contrario, desde la identidad estable `(source_mailbox, external_message_id)`. La decisión de correlación tiene prioridad: una conversación nunca fuerza reutilización ante conflicto funcional. `CaseMessage` conserva rol, método, confianza y motivo. La bandeja E muestra una fila por `ExceptionCase`; el detalle presenta una cronología de `CaseMessage` y las `EmailException` vinculadas. Las acciones siguen dirigidas a cada `EmailException` y la auditoría operativa se muestra separada de la cronología de mensajes. Los detalles de excepción por PK continúan disponibles por compatibilidad y usan el mismo permiso de lectura.

La persistencia está dentro de `transaction.atomic(using=...)`. La evidencia se publica en `CandidateRetrievalSession` mediante `transaction.on_commit(..., using=...)`; rollback de la transacción o savepoint descarta el callback. Las consultas ORM y callbacks quedan ligados al mismo alias. Las claves únicas y `get_or_create` respaldan reintentos por `(source_mailbox, external_message_id)` y `(case, email)`.

## Ciclo de vida operativo — Fase F.1

`ExceptionCase.status` conserva `PENDING` únicamente por compatibilidad histórica; no se usa como destino de nuevas transiciones. Los nuevos casos nacen en `OPEN`. El flujo operativo permitido es `OPEN → IN_PROGRESS/WAITING`, `IN_PROGRESS → WAITING/RESOLVED`, `WAITING → IN_PROGRESS/RESOLVED`, `RESOLVED → CLOSED/IN_PROGRESS` y `CLOSED → IN_PROGRESS`. La transición desde `PENDING` a `IN_PROGRESS` es la única salida legacy permitida. Resolver exige motivo; reabrir exige motivo y limpia `resolved_at`, que representa la resolución actual. Cerrar registra `closed_at` y solo es posible desde `RESOLVED`.

Las transiciones se ejecutan mediante `email_exceptions.case_workflow.transition_case`, que valida el contexto operativo (SSO heredado confiable o permiso local `operate_email_exceptions`), recupera el caso con bloqueo de fila dentro de `transaction.atomic(using=...)` y crea `CaseActivity` en la misma transacción. `CaseActivity` es auditoría del ciclo de vida y no reemplaza `CaseMessage` (comunicaciones) ni `EmailAuditEvent` (acciones de una excepción). F.1 no registra automáticamente `CASE_CREATED` durante ingestión ni modifica la correlación; la llegada de correos sobre casos `RESOLVED` o `CLOSED` conserva la política actual y queda pendiente de una fase posterior.

### Gestión operativa — Fase F.2

Cada `ExceptionCase` puede tener un responsable Django (`assigned_to`/`assigned_at`) y datos de seguimiento (`next_action` de hasta 500 caracteres y `follow_up_at`). Solo usuarios activos aparecen como responsables asignables. La toma, asignación, reasignación y desasignación son acciones explícitas y no cambian el estado del caso; ejecutarlas requiere el contexto operativo validado o el permiso local correspondiente.

`assign_case`, `take_case`, `unassign_case`, `update_case_follow_up` y `add_case_note` viven en `case_workflow.py`, usan `transaction.atomic(using=...)` y bloqueo de fila. Las actividades `ASSIGNED`, `REASSIGNED`, `UNASSIGNED`, `FOLLOW_UP_UPDATED` y `NOTE_ADDED` se guardan junto con la mutación. Las notas internas son append-only, tienen un máximo de 4.000 caracteres y no son correos, `CaseMessage`, tareas Zoho ni envíos externos.

Al resolver o cerrar se limpian `next_action` y `follow_up_at` dentro de la misma transición; una reapertura no restaura el seguimiento anterior. F.2 no implementa scheduler, SLA, colas, notificaciones, automatizaciones ni creación de Tasks Zoho.

## Backfill histórico aislado

`email_exceptions_backfill` es una herramienta explícita de importación histórica. Sin `--persist` opera como dry-run transaccional que revierte; con `--persist` exige un alias distinto de `default` y valida que la base física no coincida con la predeterminada. Acepta `--offset`/`--limit`, procesa en orden estable y usa una sola `CandidateRetrievalSession` por ejecución. No llama servicios externos ni Zoho. En local se conserva el alias configurable `backfill` (SQLite mediante `BACKFILL_SQLITE_PATH` o PostgreSQL mediante variables dedicadas `BACKFILL_DB_*`); aliases temporales de validación no forman parte de settings.

No hacer backfill en producción sin una aprobación y un plan de restauración independientes. Los XLSX reproducibles de replay/backfill permanecen fuera de Git; las referencias históricas bajo `docs/email_exceptions/reference/` tampoco se cargan en runtime.
