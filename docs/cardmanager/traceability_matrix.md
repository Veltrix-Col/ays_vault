# Matriz de trazabilidad de CardManager

| ID | Requisito | Estado | Componente | Prueba/evidencia | Observación / pendiente |
|---|---|---|---|---|---|
| CM-01 | Contraseña + MFA | Implementado y probado automáticamente | `auth_views.py`, `identity.py` | `VaultIdentitySecurityTests` | QA con autenticador real pendiente |
| CM-02 | Recuperación de MFA | Implementado y probado automáticamente | `MFARecoveryCode` | pruebas de single-use/regeneración | Runbook A&S pendiente |
| CM-03 | Sesión segura e inactividad | Implementado y probado | middleware/modelos | pruebas de sesión y concurrencia | Validar PostgreSQL real |
| CM-04 | Roles y anti-IDOR | Implementado y probado | decoradores/vistas | pruebas de matriz e IDOR | Matriz funcional por aprobar |
| CM-05 | Cifrado y duplicados | Implementado y probado | `crypto.py`, `PaymentCard`, formularios | prueba cifrado/Luhn/duplicado | KMS pendiente |
| CM-06 | Revelado/copia protegidos | Implementado y probado | `security.py`, vistas | grants/contextos/single-use | QA manual pendiente |
| CM-07 | Horario/festivos/excepciones | Implementado y probado | `policies.py` | `PolicyAndScheduleTests` | Calendario operativo pendiente |
| CM-08 | Auditoría encadenada | Implementado y probado | auditoría/modelos/comando | prueba de manipulación | Anclaje externo pendiente |
| CM-09 | Alertas y correo | Implementado/configurable | alertas/notificaciones | suites SMTP/Graph | Credenciales/entrega real pendiente |
| CM-10 | XLSX/PDF seguros | Implementado y probado | `reporting.py` | `ReportingTests` | Carga real pendiente |
| CM-11 | Producción PostgreSQL/HTTPS | Configurado | `settings.py`, Docker | validaciones de settings | Despliegue no confirmado |
| CM-12 | Backup/restore | Pendiente de infraestructura | Compose/operación | sin evidencia automática | Definir RPO/RTO y ensayo |
