# Visión general de CardManager

## Problema de negocio

CardManager sustituye la consulta informal de datos de tarjetas por una custodia centralizada, cifrada, controlada por rol y auditable. Permite registrar, buscar y desactivar tarjetas, consultar datos protegidos bajo una operación justificada y supervisar el uso.

## Alcance implementado

- Alta, edición, búsqueda, detalle y desactivación lógica de tarjetas.
- PAN enmascarado por defecto; PAN, vencimiento y “Código” cifrados.
- Detección de duplicados por HMAC/fingerprint del PAN y validación Luhn.
- Contraseña, TOTP, recuperación, sesión segura e inventario de dispositivos.
- Horarios, festivos, excepciones y reautenticación de operaciones sensibles.
- Roles Administrador, Líder de cartera y Analista.
- Auditoría encadenada, alertas, centro de control, timeline y exportes XLSX/PDF.
- Correo por consola, SMTP o Microsoft Graph según configuración.

## Estado verificable

| Área | Estado |
|---|---|
| Implementación Django | Implementada |
| Migraciones `vault` 0001–0013 | Presentes; aplicación real por ambiente no verificada aquí |
| Pruebas automatizadas | Existe cobertura extensa; resultado de ejecución de esta intervención sujeto al entorno |
| Prueba manual de producción | No realizada en esta intervención |
| Correo externo | Implementado y configurable; credenciales/entrega real no confirmadas |
| Despliegue Docker/PostgreSQL | Preparado; estado desplegado no confirmado |

## Fuera de alcance

CardManager no documenta ni implementa procesamiento SOAT, Colectivos ni escritura Zoho. Tampoco se afirma cumplimiento regulatorio o PCI DSS certificado: eso requiere evaluación independiente.
