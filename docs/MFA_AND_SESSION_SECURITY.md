# MFA y seguridad de sesiones

## Flujo de autenticación

1. El usuario presenta credenciales al endpoint propio de login, integrado con Django Auth y Axes.
2. La contraseña correcta crea únicamente `preauth_user_id` y una marca temporal de cinco minutos; todavía no existe usuario autenticado.
3. Un usuario sin MFA activo va al enrolamiento. Un usuario activo va a verificación TOTP/recuperación.
4. Solo después del segundo factor se ejecutan `login()` y `django_otp.login()`, se registra el dispositivo y se crea la sesión segura.
5. Cualquier vista autenticada exige usuario verificado y una `SecureSession` activa.

## Enrolamiento

Se crea un `TOTPDevice` no confirmado. El QR se produce como data URI en memoria mediante Segno y la clave manual se muestra en esa respuesta. Se exige nuevamente la contraseña y un primer TOTP. Solo entonces el dispositivo queda confirmado, se generan diez códigos y se inicia sesión. El usuario debe confirmar que guardó los códigos antes de entrar a la bóveda.

## Recuperación

Los códigos se generan con entropía criptográfica, se almacenan con el hasher de Django y se consumen en transacción. La regeneración invalida todos los anteriores, exige reautenticación `mfa_manage`, se muestra una vez y genera auditoría/alerta. No hay descarga persistida en servidor.

## Reinicio administrativo

El Administrador debe completar reautenticación `identity_admin` y registrar motivo. El flujo elimina TOTP/códigos, revoca sesiones Django y registros seguros, invalida grants/revelados, degrada confianza de dispositivos y deja el perfil pendiente de enrolamiento. Nunca muestra secretos ni cambia permisos sobre tarjetas.

## Sesión única y revocación

Cada sesión guarda SHA-256 del identificador y una copia cifrada únicamente para eliminar la fila real de `django_session`. Un login nuevo revoca todas las sesiones anteriores. La gestión visual de sesiones y dispositivos es exclusiva del Administrador; Líder y Analista trabajan solo en Bóveda. Las acciones administrativas conservan reautenticación y motivo cuando corresponde.

## Inactividad

El límite predeterminado es 600 segundos. `last_activity_at` se actualiza como máximo una vez por minuto. Al expirar se invalidan sesión Django, grants y revelados, se audita y se redirige con mensaje genérico.

## Ventana transversal de operaciones sensibles

Los propósitos administrativos actuales incluyen `cards_manage`, `identity_admin`, `session_manage`, `device_manage`, `alerts_manage`, `password_change`, `mfa_manage`, `policy_admin` y `outside_hours`. Una validación correcta de contraseña y TOTP abre una única `SensitiveOperationWindow` fija de 15 minutos, ligada al usuario y al hash de la sesión. Los propósitos siguen delimitando la intención y la auditoría, pero una ventana vigente evita repetir factores en la siguiente operación sensible autorizada. La vigencia no se extiende con cada acción.

`PendingSensitiveOperation` guarda la identidad y el estado de una acción específica cuando una redirección es necesaria. Nueva/Editar tarjeta cifran temporalmente su formulario; PAN, vencimiento y empresa nunca se colocan en URL, sesión o auditoría y el payload se borra al completar o expirar. El UUID, usuario, sesión, propósito, objeto y estado de consumo garantizan ejecución única.

Empresa, PAN y vencimiento mantienen autorizaciones adicionales. Cada operación de revelado/copia exige un motivo y una referencia nuevos y crea un `ProtectedOperationContext` temporal exclusivo para esa tarjeta. Los tres campos comparten ese contexto mientras se permanezca en la misma operación. Volver a la Bóveda, confirmar otra tarjeta, expirar la ventana o invalidar la sesión cierra el contexto. `RevealGrant` limita cada campo; el valor se retira del DOM a los 20 segundos o al ocultarse/cambiar de pestaña y la copia mantiene token de un solo uso.

Cambiar politicas, festivos, excepciones, destinatarios o reintentar correo exige `policy_admin`. La politica de sesion permite revocar la anterior, bloquear una nueva o conservar sesiones hasta el limite configurado. El valor predeterminado mantiene una sola sesion y revoca la anterior.

## Procedimientos

- Pérdida del autenticador: usar una recuperación de un solo uso o solicitar reinicio administrativo verificado.
- Sospecha de sesión: reautenticar y revocar la sesión o todas.
- Dispositivo perdido: bloquearlo; esto revoca sus sesiones. El desbloqueo es administrativo.
- Cambio de contraseña: revoca otras sesiones, autorizaciones y confianza de dispositivos.
- Nunca solicitar, registrar o enviar por correo TOTP, secreto o códigos de recuperación.

## Variables y dependencias

`SESSION_INACTIVITY_SECONDS`, `SESSION_ACTIVITY_THROTTLE_SECONDS`, `REAUTH_TTL_SECONDS`, `MFA_FAILURE_LIMIT` y `MFA_ISSUER`. Dependencias: `django-otp==1.7.0`, `segno==1.6.6`; `pip-audit==2.10.1` solo para desarrollo.

## Límites

No existe recuperación por correo habilitada, KMS, SIEM inmutable ni certificación productiva. Se requieren pruebas PostgreSQL/concurrencia, pentest y QA visual. No usar datos reales todavía. El requerimiento empresarial adicional de código de seguridad de tarjeta sigue sin implementar hasta decisión formal.
