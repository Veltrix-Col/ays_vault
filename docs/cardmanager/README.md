# CardManager — documentación vigente

**Versión documental:** 1.0

**Actualización:** 2026-08-05

**Responsable técnico:** equipo de ingeniería A&S/Veltrix; propietario funcional por confirmar.

## Objetivo y estado

Este directorio documenta el estado comprobable de CardManager a partir de `vault/`, `config/`, sus migraciones, plantillas y pruebas. CardManager custodia datos de tarjetas, controla su consulta y mantiene trazabilidad. El código implementa autenticación con MFA, sesiones seguras, roles, operaciones sensibles, auditoría encadenada, alertas y exportes. La preparación de producción existe; credenciales, infraestructura, restauración y validación operativa real requieren confirmación de A&S.

Flujo resumido: contraseña → MFA → sesión segura → control de rol/horario → consulta enmascarada → reautenticación y contexto de operación → revelado/copia temporal → auditoría.

## Índice

- [Visión general](overview.md)
- [Arquitectura funcional](functional_architecture.md)
- [Arquitectura técnica](technical_architecture.md)
- [Modelo de datos](data_model.md)
- [Modelo de seguridad](security_model.md)
- [Roles y permisos](roles_and_permissions.md)
- [Autenticación y MFA](authentication_and_mfa.md)
- [Auditoría y monitoreo](audit_and_monitoring.md)
- [Reportes y exportes](reports_and_exports.md)
- [Configuración](configuration.md)
- [Despliegue](deployment.md)
- [Operación](operations.md)
- [Backup y recuperación](backup_and_recovery.md)
- [Pruebas](testing.md)
- [Limitaciones conocidas](known_limitations.md)
- [Mejoras pendientes](pending_improvements.md)
- [Matriz de trazabilidad](traceability_matrix.md)
- [Transferencia técnica](technical_handover.md)

## Advertencias

- No se revisó ningún `.env`; este documento no confirma credenciales instaladas.
- El campo de negocio mostrado como **Código** está implementado y cifrado, pero el repositorio no autoriza llamarlo CVV. Su significado y política de retención deben validarse con A&S.
- SQLite es únicamente una opción de desarrollo; fuera de `DEBUG` la configuración exige PostgreSQL.
- Los documentos históricos de la raíz de `docs/` se conservan como antecedentes; este índice es la referencia vigente.

