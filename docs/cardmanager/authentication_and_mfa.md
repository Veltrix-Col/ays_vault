# Autenticación, MFA y sesiones

## Flujo

1. `password_login` valida usuario/contraseña.
2. Si no hay TOTP confirmado, `mfa_enroll` genera el QR con issuer `CardManager` y exige confirmación.
3. `mfa_verify` admite TOTP o código de recuperación válido.
4. Se crea una `SecureSession` y se registra dispositivo/IP de forma saneada.
5. `SecureSessionMiddleware` valida sesión, MFA, recuperación pendiente, inactividad y bloqueo.

## Recuperación y administración

Se generan diez códigos aleatorios; solo sus hashes persisten y cada uno se consume una vez. Regenerarlos invalida los anteriores. El reset administrativo revoca sesiones, dispositivos/confianza y TOTP previo. Tras enrolamiento, los códigos deben confirmarse en la pantalla específica.

## Sesiones y dispositivos

La sesión Django no se guarda en claro dentro de `SecureSession`. Cambio de contraseña, logout, reset MFA o reemplazo de sesión invalidan autorizaciones sensibles. Un dispositivo confiable no omite MFA. El middleware expira por inactividad y bloquea dispositivos bloqueados.

## Reautenticación

Las operaciones sensibles usan una ventana temporal transversal y un contexto por tarjeta/referencia. Los tokens y contextos están ligados a propósito, usuario y sesión; expiración o cambio de sesión los invalida.

## Pruebas manuales pendientes

Enrolamiento con autenticador real, recuperación de cuenta, concurrencia en PostgreSQL, cambio de dispositivo/IP y expiración con reloj operativo deben validarse por A&S.
