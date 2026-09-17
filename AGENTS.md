# Política arquitectónica de acceso A&S

- Clasificar cada herramienta nueva como herramienta interna general o como
  boundary especial antes de implementar autorización.
- Las herramientas internas generales deben reutilizar el acceso delegado de
  Intranet expuesto por `config.application_access` y aplicado por
  `config.middleware.TrustedIntranetAccessMiddleware`. No crear permisos,
  grupos, roles, perfiles, flags por módulo ni middleware paralelo sin una
  necesidad funcional o de seguridad explícita.
- `TOOLS_ACCESS_MODE=trusted_intranet` exige validación SSO confiable en
  Production. `TOOLS_ACCESS_MODE=local_public` solo puede operar con
  `DEBUG=true` o durante tests; nunca es un bypass productivo.
- Vault/Card Manager es un boundary independiente: conserva UserProfile,
  roles, MFA/TOTP, reautenticación, sesiones seguras, dispositivos, auditoría
  y sus controles operativos. El acceso SSO general no concede acceso a Vault.
- El acceso a una aplicación no sustituye los controles de acciones concretas:
  POST, CSRF, permisos funcionales, guards de Zoho y transacciones siguen
  siendo independientes.
- Los endpoints M2M/inbound no deben heredar autenticación humana; deben usar
  su token, validaciones, límites e idempotencia propios.

Antes de añadir autorización, verificar si el request ya pasó el gate general,
si se está reutilizando accidentalmente Vault, y si local y Production siguen
siendo fronteras separadas.
