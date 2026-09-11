# Excepciones de Facturación

El módulo reconstruye datos con lecturas de Zoho CRM **Production**, reutiliza
los motores legacy y guarda un snapshot local. La UI nunca ejecuta la
reconstrucción ni depende de Excel, CSV o Analytics.

## Contratos

- Una excepción técnica conserva su `billing-exception/v1` original.
- Un caso con operación usa `billing-case/v1/operation/{policy}/operation:{id}`.
- Una operación faltante usa `billing-case/v1/missing-operation/{policy}/installment:{n}@{fecha}`.
- Un cobro faltante usa `billing-case/v1/policy-gap/{policy}/policy-gap`.
- Prioridad 1: operación o cobro faltante; prioridad 2: varios hallazgos;
  prioridad 3: un hallazgo estándar. Luego se ordena por fecha (nulos al
  final), póliza y `case_key`.

Un refresh se encola en estado `PENDING` y se procesa con:

```console
python manage.py colectivos_refresh_billing_exceptions --run-id ID
```

Para consumir automáticamente las solicitudes creadas desde la interfaz, se
ejecuta en un proceso separado y persistente:

```console
python manage.py colectivos_billing_exceptions_worker --poll-seconds 5
```

El consumidor procesa una solicitud a la vez, reutiliza el mismo ejecutor y
marca como fallida una ejecución `RUNNING` que lleve más de 15 minutos. Para
una ejecución puntual de operación o diagnóstico puede usarse `--once`.

En Production, Dokploy debe programar además el siguiente comando todos los
días a las 08:00 a. m., usando la zona horaria configurada por Django
(`America/Bogota`):

```console
python manage.py colectivos_queue_billing_exceptions_refresh
```

Cron conceptual: `0 8 * * *`. Este disparador únicamente crea un run
`PENDING` con la fecha local y termina; no consulta Zoho ni ejecuta los
motores. Si ya existe un run `PENDING` o `RUNNING`, termina correctamente sin
crear otro. El worker permanente es quien toma el run y ejecuta el refresh.
La configuración efectiva del scheduler en Dokploy queda como paso de
despliegue posterior.

También puede crearse y ejecutarse en una sola invocación, siempre con fecha
explícita:

```console
python manage.py colectivos_refresh_billing_exceptions --as-of YYYY-MM-DD
```

Solo un run Production puede estar pendiente/en curso. Únicamente un resultado
completo y exitoso cambia detecciones a `NOT_DETECTED`. Una reaparición restaura
`DETECTED`, conserva historia y nunca cierra o reabre Tasks.

## Tasks

El builder acepta solamente `Subject`, `tipo_de_solicitud`,
`Caso_de_excepci_n`, `Motivo_de_excepci_n`, `N_mero_p_liza`, `rea`,
`Observaciones`, `Ramo`, `Aseguradora1`, `Responsable` y
`Correo_responsable`. Usa `Facturación`, `true` y `Cartera`; nunca envía
`Priority`, `Owner`, `Who_Id`, `What_Id` ni Notes. Responsable es opcional y,
si se selecciona, debe coincidir con un `actual_value` de Production.

La intención queda cifrada e idempotente en el outbox compartido. Durante esta
fase la UI deja la acción protegida: cualquier WRITE real requiere perfil
Sandbox, feature flag y confirmación de los guards existentes. Habilitar Tasks
en Production en el futuro exige autorización explícita y la configuración de
Production correspondiente; este cambio no la habilita.

Si un run falla, consulte `safe_error` y ejecute uno nuevo; el snapshot válido
anterior permanece disponible. El consumidor persistente debe ejecutarse como
un proceso separado del servidor web.
