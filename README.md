# A&S Bóveda de medios de pago

Base funcional independiente construida con Django para custodiar datos de tarjetas cifrados, enmascarados y auditados. Reutiliza únicamente la identidad visual del portal A&S.

## Funcionalidades incluidas

- Roles individuales: administrador, líder de cartera y analista.
- Solo el líder puede registrar tarjetas.
- Número y vencimiento cifrados con Fernet.
- No existe campo para CVV/CVC.
- Revelado temporal con contraseña y motivo obligatorio.
- Copia por campo y registro de auditoría.
- Logs de acceso, consulta, revelado, copia y creación.
- Alertas por correo para eventos sensibles fuera de horario.
- Sesión expira tras 10 minutos de inactividad.
- Bloqueo de fuerza bruta con django-axes.
- 30 tarjetas completamente ficticias para pruebas.

## Inicio rápido (PowerShell)

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# Pegue la llave en FIELD_ENCRYPTION_KEY del .env
python manage.py makemigrations vault
python manage.py migrate
python manage.py seed_demo
python manage.py test
python manage.py runserver
```

Usuarios demo (misma clave): `DemoSeguro2026!`

- `adminvault`: administra usuarios y consulta auditoría. No ve tarjetas.
- `lidercartera`: crea y consulta tarjetas.
- `analistacartera`: consulta, revela y copia con trazabilidad.

## Límites antes de producción

Esta entrega es una base funcional de desarrollo, no una certificación PCI DSS. Antes de producción se requiere PostgreSQL administrado, KMS/Key Vault, MFA real, VPN o allowlist, correo transaccional, logs externos append-only, backups cifrados, CSP estricta, EDR en equipos y revisión formal de cumplimiento. La llave `FIELD_ENCRYPTION_KEY` no debe rotarse manualmente sin un procedimiento de recifrado.
