# Pruebas de CardManager

## Cobertura existente

Las suites en `vault/tests*.py` cubren autenticación/MFA, sesiones, concurrencia SQLite, roles/IDOR, cifrado y duplicados, revelado/copia, ventanas sensibles, políticas, alertas, correo, control, UI, reportes, tareas y flujo integral. Las pruebas de correo aíslan la suite de SMTP/Graph reales.

## Ejecución

```powershell
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test vault
python manage.py verify_audit_chain
```

Para producción, agregue `python manage.py check --deploy` en un entorno con variables no secretas válidas. No ejecute `seed_demo` en producción.

## Manual mínimo

Login incorrecto/correcto, enrolamiento y recuperación; segundo login y expiración; matriz de roles; horario/excepción; revelar/copiar y expiración del grant; bloqueo de IDOR/CSRF; reportes; alerta y correo de prueba; responsive y branding.

## Estado de esta actualización

No se modificó código. El inventario de pruebas fue revisado estáticamente. El resultado efectivo de ejecución se registra en la entrega final; no debe interpretarse la existencia de pruebas como QA manual aprobado.
