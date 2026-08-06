# Modelo de datos de CardManager

## Dominio y políticas

- `PaymentCard`: empresa administrativa, alias, titular, franquicia, propósito, estado, PAN/vencimiento/Código cifrados, fingerprint y últimos cuatro.
- `UserProfile`: rol y datos de operación.
- `PolicyConfiguration`: horario semanal, sesiones, inactividad y comportamiento fuera de horario.
- `Holiday`, `AccessException`: calendario y excepciones temporales.

## Identidad y operaciones sensibles

- `UserDevice`: fingerprint, estado, confianza y metadatos reducidos del dispositivo.
- `SecureSession`: sesión segura, expiración, dispositivo y estado MFA; la clave de sesión Django se almacena cifrada.
- `MFARecoveryCode`: hash y marca de consumo.
- `ReauthenticationGrant`, `RevealGrant`: permisos temporales ligados a usuario/sesión/propósito.
- `SensitiveOperationWindow`, `ProtectedOperationContext`, `PendingSensitiveOperation`: ventana de identidad, contexto por tarjeta y reanudación idempotente de POST sensible.

## Auditoría y operación

- `AuditEvent`, `AuditChainState`, `AuditVerificationRun`: eventos, estado y verificaciones de cadena hash.
- `SecurityAlert`, `AlertTransition`: alertas y su historial.
- `NotificationRecipient`, `NotificationRecord`: destinos y entregas saneadas.
- `ReportExport`: solicitudes/resultado de exportación.
- `PolicyEvaluationRun`: ejecución del evaluador periódico.

## Migraciones

Existen `0001_initial` a `0013_clear_recovered_legacy_company`. La migración 0012 introdujo `company_name` y `encrypted_code`; la 0013 limpia una recuperación histórica de empresa. No se creó ni modificó ninguna migración durante esta actualización documental.

## Datos sensibles

El PAN se valida con Luhn, se cifra y se fingerprinta para duplicados. Vencimiento y “Código” se cifran. La etiqueta del código no demuestra que sea CVV; A&S debe definir su semántica y retención. Nunca se debe documentar ni registrar valores reales.
