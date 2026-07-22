# Entrega 01 — A&S Vault

## 1. Identificación de la entrega

| Campo | Información |
|---|---|
| Proyecto | A&S Vault |
| Cliente | A&S |
| Proveedor | Veltrix S.A.S. |
| Versión | `0.1.0` |
| Etiqueta Git | `v0.1.0-entrega-1` |
| Fecha de entrega | 21 de julio de 2026 |
| Tipo de entrega | Primera entrega funcional y técnica |
| Repositorio | `[COMPLETAR URL DEL REPOSITORIO PRIVADO]` |
| Commit entregado | `[COMPLETAR HASH DEL COMMIT]` |
| Responsable Veltrix | Camilo Vargas |
| Estado | Entrega para revisión controlada; no habilitada para producción |

## 2. Objeto de la entrega

Veltrix S.A.S. entrega la primera versión funcional y técnica de **A&S Vault**, una aplicación interna desarrollada en Django para la custodia controlada de información sensible asociada a medios de pago.

Esta entrega comprende la arquitectura base, controles de autenticación y autorización, cifrado de campos sensibles, auditoría, monitoreo, políticas de acceso, alertas e informes. Su propósito es permitir la revisión funcional, técnica y de seguridad por parte de A&S en un ambiente controlado.

Esta versión **no constituye una certificación PCI DSS**, no debe utilizarse todavía con información real y no se considera autorizada para operación productiva.

## 3. Alcance funcional incluido

### 3.1 Autenticación y seguridad de acceso

- Inicio de sesión en dos etapas.
- MFA TOTP obligatorio mediante aplicación autenticadora.
- Enrolamiento MFA con código QR generado en memoria.
- Diez códigos de recuperación almacenados únicamente mediante hash.
- Códigos de recuperación de un solo uso.
- Límite de intentos fallidos de MFA.
- Reautenticación para operaciones críticas.
- Expiración de sesión por inactividad.
- Restricción a una sesión activa por usuario.
- Revocación real de sesiones.
- Inventario de dispositivos y sesiones.
- Estados de dispositivo: Nuevo, Reconocido, Bloqueado y Revocado.

### 3.2 Roles y separación de funciones

- **Administrador:** administra seguridad, usuarios, sesiones, dispositivos, políticas, alertas y monitoreo; no puede consultar ni revelar tarjetas.
- **Líder de cartera:** crea, modifica, consulta, revela, copia y desactiva tarjetas según los controles definidos.
- **Analista:** consulta exclusivamente tarjetas activas y utiliza las operaciones expresamente autorizadas.
- Validación de permisos en backend.
- Protección contra acceso directo no autorizado a recursos.
- Separación entre administración de seguridad y operación de tarjetas.

### 3.3 Custodia de información sensible

- Cifrado de PAN y fecha de vencimiento mediante Fernet.
- Llaves separadas para cifrado y huella HMAC.
- Validación de PAN mediante algoritmo de Luhn.
- Identificación de franquicia.
- Detección de registros duplicados mediante HMAC.
- Visualización enmascarada.
- Revelado temporal, controlado y auditable.
- Autorizaciones de revelado ligadas a usuario, sesión, tarjeta y propósito.
- Copia controlada y auditable.
- Desactivación lógica de tarjetas.

### 3.4 Auditoría y trazabilidad

- Registro de accesos y acciones sensibles.
- Registro de revelados y copias.
- Registro de intentos fallidos.
- Registro de cambios administrativos.
- Registro de exportaciones.
- Cadena de auditoría secuencial con hash encadenado.
- Comando de verificación de integridad:

```powershell
python manage.py verify_audit_chain
```

### 3.5 Centro de Control

- Resumen del estado de seguridad y operación.
- Estado de integridad de auditoría.
- Estado de MFA y adopción.
- Estado de base de datos y correo.
- Alertas operativas.
- Excepciones y políticas.
- Salud operativa.
- Línea de tiempo con filtros, orden y paginación.
- Alcance de información restringido según el rol.

### 3.6 Políticas de acceso

- Horarios configurables.
- Configuración diferenciada para semana, sábado y domingo.
- Zona horaria `America/Bogota`.
- Gestión de festivos.
- Gestión de excepciones.
- Evaluación de inactividad.
- Evaluación de usuarios sin uso o sin MFA.
- Evaluación de vencimientos y alertas.
- Ejecución idempotente mediante comando administrativo.

### 3.7 Alertas y correo

- Alertas persistentes por eventos de seguridad.
- Alertas por dispositivo o IP nueva.
- Alertas por fallos de MFA y reautenticación.
- Alertas por uso de recuperación.
- Alertas por reinicio de MFA.
- Alertas por sesiones reemplazadas.
- Alertas por bloqueos y cambios sensibles.
- Backend de correo intercambiable.
- Preparación técnica para Microsoft Graph con OAuth 2.0.
- Registro de fallos de envío y reintentos.

### 3.8 Informes y exportaciones

- Informe de Línea de Tiempo.
- Informe de Alertas.
- Informe de Accesos.
- Informe de Adopción.
- Informe administrativo de Tarjetas Seguras, sin Empresa, PAN ni vencimiento.
- Informe de Salud Operativa para el Administrador.
- Exportación Excel mediante `openpyxl`.
- Exportación PDF mediante `WeasyPrint`.
- Generación en memoria.
- Protección CSRF.
- Límites de filas y periodo consultado.
- Neutralización de fórmulas en Excel.
- Registro auditable de cada exportación.

## 4. Componentes técnicos entregados

- Proyecto Django.
- Aplicación principal `vault`.
- Migraciones de base de datos.
- Plantillas HTML.
- Recursos estáticos.
- Pruebas automatizadas.
- Comandos de administración.
- Configuración mediante variables de entorno.
- Archivo `.env.example`.
- Dependencias fijadas en `requirements.txt`.
- Dependencias de desarrollo en `requirements-dev.txt`.
- Documentación técnica y operativa.
- Datos de demostración generables mediante comando.

## 5. Documentación incluida

- `README.md`
- `CHANGELOG.md`
- `docs/SECURITY_ARCHITECTURE.md`
- `docs/MFA_AND_SESSION_SECURITY.md`
- `docs/CONTROL_CENTER.md`
- `docs/ALERTING_AND_EMAIL.md`
- `docs/ACCESS_POLICIES.md`
- `docs/REPORTING_AND_EXPORTS.md`
- `docs/ENTREGA_01.md`

## 6. Instalación de revisión

### 6.1 Requisitos

- Python compatible con las versiones definidas por el proyecto.
- Entorno virtual independiente.
- Dependencias instaladas desde `requirements-dev.txt`.
- Variables de entorno propias.
- SQLite exclusivamente para desarrollo y revisión local.

### 6.2 Preparación

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
```

Deben generarse valores independientes para:

- `SECRET_KEY`
- `FIELD_ENCRYPTION_KEY`
- `FIELD_FINGERPRINT_KEY`

No deben reutilizarse llaves de ambientes anteriores ni valores compartidos por correo, chat o repositorio.

### 6.3 Inicialización

```powershell
python manage.py migrate
python manage.py seed_demo
python manage.py check
python manage.py test
python manage.py verify_audit_chain
python manage.py runserver
```

Los datos creados por `seed_demo` son ficticios y deben utilizarse únicamente para revisión.

## 7. Validaciones previas a la entrega

Antes de publicar la etiqueta Git de esta entrega deben ejecutarse y registrarse los siguientes controles:

```powershell
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py migrate
python manage.py test
python manage.py verify_audit_chain
python -m pip check
python -m pip_audit
git diff --check
git status
```

### Evidencia de validación

| Validación | Resultado |
|---|---|
| `python manage.py check` | `[COMPLETAR]` |
| Migraciones pendientes | `[COMPLETAR]` |
| Pruebas automatizadas | `[COMPLETAR NÚMERO DE PRUEBAS Y RESULTADO]` |
| Integridad de auditoría | `[COMPLETAR]` |
| `pip check` | `[COMPLETAR]` |
| `pip-audit` | `[COMPLETAR]` |
| Revisión visual | `[COMPLETAR]` |
| Commit entregado | `[COMPLETAR]` |

## 8. Archivos excluidos de la entrega

La entrega no debe contener ni rastrear:

```text
.env
*.sqlite3
*.db
.venv/
venv/
__pycache__/
*.pyc
media/
staticfiles/
.pytest_cache/
.coverage
htmlcov/
```

Tampoco deben incluirse:

- Llaves reales.
- Contraseñas.
- Credenciales OAuth.
- Secretos de Microsoft Graph.
- Bases de datos locales.
- Información real de titulares o tarjetas.
- Logs locales con información operativa.
- Copias de respaldo.

## 9. Exclusiones y riesgos pendientes

Esta primera entrega no incluye autorización para producción. Permanecen pendientes:

- KMS, Azure Key Vault o mecanismo equivalente.
- Rotación operativa de llaves.
- PostgreSQL validado bajo concurrencia.
- Infraestructura productiva endurecida.
- Auditoría externa inmutable o integración SIEM.
- Backups cifrados y pruebas de restauración.
- VPN, allowlist o controles de red equivalentes.
- Pentest externo.
- QA visual completo en navegadores y dispositivos objetivo.
- Validación de WeasyPrint en el servidor final.
- Configuración y prueba real de Microsoft Graph.
- Diseño formal de recuperación de contraseña.
- Revisión contractual y formal de cumplimiento.
- Certificación PCI DSS.
- Decisión formal sobre cualquier código adicional de seguridad asociado a tarjetas.

## 10. Restricciones de uso

Hasta que se cierre la etapa de preparación productiva:

1. No cargar datos reales.
2. No almacenar códigos adicionales de seguridad de tarjetas.
3. No desplegar públicamente.
4. No reutilizar las llaves incluidas en ambientes de desarrollo.
5. No compartir bases SQLite.
6. No habilitar correo real sin credenciales y permisos controlados.
7. No interpretar esta entrega como certificación de cumplimiento.

## 11. Repositorio y control de versión

La fuente oficial de esta entrega será el repositorio privado de GitHub y la etiqueta:

```text
v0.1.0-entrega-1
```

La etiqueta debe apuntar al commit aprobado y entregado. Cualquier cambio posterior debe quedar identificado en una nueva versión o etiqueta.

Comandos sugeridos:

```powershell
git add .
git commit -m "release: primera entrega funcional de A&S Vault"
git push origin main
git tag -a v0.1.0-entrega-1 -m "Primera entrega funcional de A&S Vault"
git push origin v0.1.0-entrega-1
```

## 12. Criterio de aceptación de esta entrega

La entrega se considera recibida para revisión cuando:

- A&S dispone de acceso autorizado al repositorio privado.
- La etiqueta de entrega está publicada.
- El código puede instalarse siguiendo la documentación.
- Las migraciones se ejecutan correctamente.
- La suite automatizada finaliza satisfactoriamente.
- La cadena de auditoría se verifica.
- Los roles y flujos principales pueden revisarse con datos ficticios.
- A&S recibe la relación de pendientes y restricciones.

La aceptación de esta entrega para revisión **no equivale a aprobación para producción**.

## 13. Observaciones de A&S

```text
[ESPACIO PARA OBSERVACIONES, HALLAZGOS Y APROBACIONES DEL CLIENTE]
```

## 14. Control de aprobación

| Parte | Nombre | Cargo | Fecha | Aprobación |
|---|---|---|---|---|
| Veltrix S.A.S. | Camilo Vargas | Responsable del proyecto | `[COMPLETAR]` | `[COMPLETAR]` |
| A&S | `[COMPLETAR]` | `[COMPLETAR]` | `[COMPLETAR]` | `[COMPLETAR]` |
