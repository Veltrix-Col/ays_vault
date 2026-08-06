# Auditoría, alertas y monitoreo

## Auditoría

`AuditEvent` registra actor, acción, resultado, riesgo, objeto seguro y metadatos saneados. Los eventos forman una cadena mediante secuencia, hash anterior y hash del evento. `AuditChainState` mantiene el extremo y `verify_audit_chain`/`AuditVerificationRun` verifican integridad.

No deben registrarse PAN, vencimiento, Código, credenciales ni claves de sesión. Los accesos HTTP 401/403 autenticados se auditan mediante middleware.

## Alertas

Se contemplan dispositivo nuevo, MFA/usuario bloqueado, acceso fuera de horario, cambios de seguridad, inactividad, uso paralelo posible, fallo de integridad, alerta crítica y fallo de correo. Las transiciones conservan comentario e historial.

## Monitoreo y salud

El centro de control muestra estado, adopción, últimos eventos, alertas abiertas, verificaciones y entregas. `/healthz/` sirve para healthcheck de despliegue. `evaluate_security_policies` evalúa políticas periódicas; su programación externa no está implementada por Django y debe configurarse en la plataforma.

## Logging

`vault.logging_utils.JSONFormatter` produce logs estructurados. Los logs de Django y Vault se envían a consola. No hay SIEM configurado en código; su integración es pendiente de infraestructura.
