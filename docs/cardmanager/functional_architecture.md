# Arquitectura funcional de CardManager

## Actores

- **Administrador:** políticas, usuarios/dispositivos/sesiones, alertas, destinatarios, reportes y control.
- **Líder de cartera:** administra tarjetas y puede consultar datos protegidos.
- **Analista:** consulta tarjetas activas y datos protegidos; no administra tarjetas ni control global.

## Flujos

1. El usuario entrega contraseña; una contraseña correcta no completa el acceso.
2. Enrolamiento o verificación TOTP; se generan códigos de recuperación de un solo uso.
3. Se crea una `SecureSession`, ligada a usuario, sesión Django y dispositivo.
4. Middleware valida MFA, sesión, inactividad, dispositivo y políticas.
5. Las listas muestran referencias seguras y últimos cuatro dígitos.
6. Revelar/copiar exige ventana de identidad y contexto de operación asociado a tarjeta.
7. El valor se entrega temporalmente con `no-store`; el permiso de copia es de un solo uso.
8. Acciones y fallos generan auditoría y, según reglas, alertas/notificaciones.

## Operación administrativa

El centro de control reúne salud, adopción, alertas, políticas, excepciones, festivos, dispositivos, sesiones, destinatarios y verificaciones. La línea de tiempo y los reportes exponen datos redactados según el rol.

## Restricciones

No hay acceso anónimo. El Administrador no hereda automáticamente acceso operativo a tarjetas. Una tarjeta inactiva no es visible al Analista. La UI no sustituye los controles del backend.
