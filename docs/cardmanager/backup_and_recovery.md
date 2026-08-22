# Backup y recuperación de CardManager

## Qué respaldar

- Base PostgreSQL completa, incluidas auditorías, sesiones, dispositivos y datos cifrados.
- Configuración no secreta y versión desplegada.
- Secretos/llaves mediante su gestor seguro, nunca dentro del backup sin protección equivalente.

Los volúmenes `vault_db_data` y `vault_media` existen en Compose; eso no constituye por sí solo un backup. CardManager no implementa un comando propio de backup/restauración.

## Requisitos

- Cifrado en tránsito y reposo, acceso mínimo, retención aprobada y copia fuera del host.
- Mantener disponibles las llaves correspondientes al backup; perder `FIELD_ENCRYPTION_KEY` hace irrecuperables los campos cifrados.
- Ensayar restauración en un ambiente aislado con usuarios y correo deshabilitados.
- Verificar migraciones, recuentos, acceso, cadena de auditoría y descifrado controlado.

