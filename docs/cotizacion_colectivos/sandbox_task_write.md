# Primera Task sintética en Zoho Sandbox

Esta fase prepara una única prueba manual. No conecta el publisher a Novedades,
Cotización Individual, miniportal ni ningún flujo funcional.

## Contrato y barreras

El único payload autorizado por `zoho_create_test_task` es:

```json
{
  "Subject": "PRUEBA VELTRIX - NO GESTIONAR",
  "tipo_de_solicitud": "Ingresos"
}
```

La creación solo puede alcanzar `zoho.records.create(module="Tasks", ...)` cuando
se cumplen simultáneamente estas condiciones:

- perfil solicitado `sandbox` y `ZOHO_ACTIVE_PROFILE=sandbox`;
- `ZOHO_SANDBOX_WRITE_ENABLED=true`;
- `COLECTIVOS_TASK_PUBLISH_ENABLED=true`;
- `COLECTIVOS_TASK_WRITE_CONFIRMATION=SANDBOX_TASK_WRITE`;
- argumento `--confirm SANDBOX_TASK_WRITE`.

`production` se rechaza antes de obtener la fachada, incluso si sus flags fueran
habilitados accidentalmente. Todos los defaults permanecen cerrados.

## Scope y preparación manual

Los scopes actuales de Vault son exclusivamente READ. Según el formato de scope
separado de Zoho CRM V8, el permiso mínimo adicional para esta prueba es:

```text
ZohoCRM.modules.tasks.CREATE
```

Debe agregarse manualmente al cliente/token exclusivo de Sandbox y requerirá el
procedimiento OAuth correspondiente fuera de esta intervención. No debe agregarse
al perfil Production.

Antes de la prueba, A&S debe confirmar nuevamente que las automatizaciones del
módulo Tasks siguen deshabilitadas en el Sandbox `Pruebas AYS`.

## Ejecución futura autorizada

```bash
python manage.py zoho_create_test_task --profile sandbox --confirm SANDBOX_TASK_WRITE
```

El comando crea como máximo un registro, no acepta JSON, módulos ni lotes. Ante
timeout o error 5xx posterior al envío, no reintenta: informa resultado incierto y
exige conciliación manual en Sandbox antes de cualquier nueva ejecución.

Los adjuntos continúan pendientes porque ays-zoho-sdk 1.1.0 no expone una fachada
pública para ellos. No se permite suplirla con HTTP directo.
