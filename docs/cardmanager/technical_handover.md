# Transferencia técnica — CardManager

**Proyecto:** `ays_tc_vault`

**Módulo:** `vault` / CardManager

**Versión documental:** 1.0

**Fecha:** 2026-08-05

## 1. Resumen ejecutivo

CardManager es una aplicación Django de custodia operativa de datos de tarjetas. Combina cifrado de campos, HMAC para duplicados, MFA TOTP, sesión propia ligada a dispositivo, políticas horarias, autorizaciones temporales para datos protegidos, auditoría encadenada, alertas y reportes. Está ampliamente probado de forma automática. La preparación de producción existe en settings/Docker, pero este handover no confirma secretos, despliegue, correo, backups, restore, pentest ni operación real.

## 2. Objetivo, alcance y estado

El objetivo es que Líderes y Analistas autorizados consulten información bajo trazabilidad, mientras Administradores supervisan la seguridad. El alcance incluye alta/edición/desactivación, búsqueda segura, revelado/copia, control de acceso, alertas y reportes. No incluye SOAT, Colectivos ni una certificación normativa.

Estados usados aquí: **implementado**, **configurable**, **probado automáticamente**, **pendiente de QA manual**, **pendiente de infraestructura**. No se equipara código existente con producción aprobada.

## 3. Arquitectura funcional y técnica

```text
Usuario
 → login/password
 → TOTP o recuperación
 → SecureSession + dispositivo
 → middleware + rol + horario
 → vista/listado enmascarado
 → reautenticación + contexto por tarjeta
 → revelado/copia temporal
 → auditoría/alerta/notificación
 → ORM → PostgreSQL (producción)
```

Las vistas nunca deben autorizar solo por UI. `role_required`, QuerySets autorizados y middleware forman el límite. Las operaciones sensibles usan una ventana de identidad reutilizable dentro de TTL, un contexto por tarjeta y grants de copia de un uso.

## 4. Componentes e inventario principal

| Componente | Responsabilidad |
|---|---|
| `vault/models.py` | Tarjetas, identidad, políticas, auditoría, alertas y reportes |
| `vault/auth_views.py`, `identity.py` | Contraseña, MFA, recuperación, sesiones/dispositivos |
| `vault/middleware.py`, `decorators.py` | Controles transversales y roles |
| `vault/views.py`, `forms.py`, `urls.py` | Casos de uso e interfaz |
| `vault/crypto.py`, `security.py` | Cifrado/fingerprint/grants |
| `vault/sensitive_operations.py` | Operaciones POST reanudables e idempotentes |
| `vault/policies.py` | Horarios, festivos y excepciones |
| `vault/audit.py`, `alerts.py` | Cadena y alertas |
| `vault/notifications.py`, `tasks.py` | Entrega SMTP/Graph y ejecución local |
| `vault/reporting.py` | XLSX/PDF saneados |
| `templates/vault`, `templates/registration`, `static` | UI, MFA y branding |

## 5. Modelo de datos

`PaymentCard` conserva empresa, alias, titular, franquicia, propósito, estado, últimos cuatro, fingerprint y tres campos cifrados: PAN, vencimiento y Código. El Código es una denominación del producto, no evidencia suficiente para clasificarlo como CVV. `UserProfile`, `PolicyConfiguration`, `Holiday` y `AccessException` controlan actor/política. `UserDevice`, `SecureSession`, `MFARecoveryCode`, grants, ventanas, contextos y pendientes modelan identidad. `AuditEvent` y estado/verificación modelan integridad. Alertas, transiciones, destinatarios, notificaciones, exportes y corridas completan operación.

Migraciones presentes: 0001–0013. No hay migraciones creadas en este trabajo documental.

## 6. Reglas de negocio y seguridad

- PAN entre 13–19 dígitos, Luhn y franquicia coherente; duplicado por fingerprint.
- Valores protegidos enmascarados salvo operación autorizada.
- Administrador, Líder y Analista no son roles acumulativos implícitos.
- TOTP se exige incluso a dispositivos confiables.
- Recuperación es hasheada, de un uso; reset MFA revoca contexto previo.
- Sesión expira por inactividad y se vincula a dispositivo/sesión Django.
- Horario/festivo puede bloquear; excepción válida puede autorizar una operación.
- POST usa CSRF; IDOR se evita filtrando objetos por actor/estado.
- Respuestas sensibles llevan no-store/no-cache/nosniff.
- Auditoría, correo y exportes deben permanecer redactados.

## 7. Integraciones

Django auth/OTP/Axes, PostgreSQL, WhiteNoise, OpenPyXL/WeasyPrint y correo. SMTP y Microsoft Graph están implementados como alternativas; Graph usa MSAL/OAuth de aplicación y HTTP controlado. En tests se fuerza backend local. La existencia de variables no confirma credenciales ni entrega real. Zoho no es dependencia funcional de CardManager.

## 8. Configuración

Variables críticas: `APP_ENV`, `DEBUG`, `SECRET_KEY`, hosts/orígenes, `DB_*`, `FIELD_ENCRYPTION_KEY`, `FIELD_FINGERPRINT_KEY`, tiempos de sesión/reauth/MFA, horario, `VAULT_BASE_URL`, correo SMTP/Graph y límites de reportes. Consulte [configuration.md](configuration.md). Nunca copie `.env` a tickets o documentación.

## 9. Operación y despliegue

Docker prepara Gunicorn y PostgreSQL. El operador debe validar HTTPS, migraciones, estáticos, políticas, festivos, destinatarios y healthcheck. Comandos disponibles: `verify_audit_chain`, `evaluate_security_policies`, `load_colombia_holidays`, `seed_demo`. El último es solo demostración. La programación periódica es externa.

## 10. Logs, observabilidad y auditoría

Logs JSON a consola; centro de control y alertas en base. La cadena detecta manipulación. No existe SIEM/anclaje WORM confirmado. En incidente: preservar evidencia, revocar acceso, verificar cadena, registrar transición y escalar sin copiar valores protegidos.

## 11. Pruebas y resultados

Las suites `vault/tests.py`, `tests_integral.py`, `tests_sensitive_operations.py`, `tests_control_center.py`, `tests_reporting.py`, `tests_email_smtp.py`, `tests_tasks.py`, `tests_mfa_concurrency.py`, `tests_ui.py` y `tests_public_home.py` cubren el sistema. Su existencia es evidencia de diseño y regresión, no de QA manual. El resultado ejecutado en esta actualización figura en el informe final de Codex; si el entorno no permite Django, debe repetirse antes de release.

## 12. Limitaciones, riesgos y pendientes

KMS, SIEM, backups/restore, scheduler durable, pentest, carga PostgreSQL, credenciales de correo, QA real y clasificación del Código siguen pendientes. La ejecución asíncrona local no es una cola durable. El fingerprint de dispositivo no es attestation. La cadena vive en la misma frontera de datos.

## 13. Soporte y recuperación

Para soporte: identificar usuario/rol, timestamp y código seguro del error; no solicitar PAN/Código. Revisar sesión/dispositivo, política, auditoría, alerta y notificación. Para recuperación: restaurar PostgreSQL y llaves compatibles en aislamiento, migrar, verificar cadena y probar descifrado controlado. RPO/RTO y responsables los define A&S.

## 14. Trazabilidad

La matriz completa está en [traceability_matrix.md](traceability_matrix.md). Los requisitos CM-01–CM-11 tienen evidencia de código/pruebas; CM-12 permanece pendiente de infraestructura.

## 15. Inventarios

- **Archivos:** los componentes de la sección 4, `config/settings.py`, `config/urls.py`, Dockerfile/Compose, migraciones y pruebas.
- **Comandos:** cuatro comandos descritos en Operación.
- **Variables:** inventario categorizado en [configuration.md](configuration.md).
- **Documentación:** índice en [README.md](README.md).

## 16. Confirmaciones y conclusión

La arquitectura implementa controles fuertes y separación de funciones, pero no se declara “lista para producción” sin validaciones externas. Esta actualización no modificó lógica, modelos, migraciones, seguridad, OAuth, Zoho, Colectivos ni Integrations; no ejecutó correo ni tráfico externo.

## Anexos

- [Modelo de seguridad](security_model.md)
- [Autenticación y MFA](authentication_and_mfa.md)
- [Backup y recuperación](backup_and_recovery.md)
- [Pruebas](testing.md)

## Índice de conformidad del handover

| Contenido obligatorio | Ubicación |
|---|---|
| 1. Portada textual | Encabezado |
| 2–5. Resumen, objetivo, alcance y estado | Secciones 1–2 |
| 6–8. Arquitecturas y componentes | Secciones 3–4 |
| 9–11. Modelo, flujo y reglas | Secciones 3, 5 y 6 |
| 12–13. Seguridad e integraciones | Secciones 6–7 |
| 14–16. Configuración, operación y despliegue | Secciones 8–9 |
| 17–18. Logs/observabilidad y auditoría | Sección 10 |
| 19–20. Pruebas y resultados | Sección 11 y entrega final de esta intervención |
| 21–24. Limitaciones, riesgos, pendientes y recomendaciones | Sección 12 y `pending_improvements.md` |
| 25–26. Soporte y recuperación | Sección 13 |
| 27. Matriz de trazabilidad | Sección 14 y `traceability_matrix.md` |
| 28–30. Archivos, comandos y variables | Sección 15 |
| 31–32. Confirmaciones y conclusión | Sección 16 |
| 33. Anexos | Lista anterior |
