# QA reproducible del flujo externo de Colectivos

## Alcance y garantías

Este procedimiento valida el portal externo, el Excel de ida y vuelta y el procesamiento de plazos. El flujo trabaja sobre el snapshot local de la solicitud y nunca crea, actualiza ni elimina registros en Zoho. No use datos personales reales en desarrollo.

## Preparación

1. Configure llaves válidas e independientes en `FIELD_ENCRYPTION_KEY` y `FIELD_FINGERPRINT_KEY`.
2. Configure correo de pruebas; en producción use el backend aprobado.
3. Mantenga `COLECTIVOS_EXTERNAL_ACCESS_VERIFICATION=otp_email` fuera de desarrollo local.
4. Revise `COLECTIVOS_EXCEL_PREVIEW_TTL_SECONDS`, `COLECTIVOS_DEADLINE_REMINDER_DAYS`, `COLECTIVOS_DEADLINE_BATCH_LIMIT` y `COLECTIVOS_DEADLINE_EMAIL_ENABLED`.
5. Aplique migraciones, reinicie Django y no abra dos servidores con configuraciones distintas en el mismo puerto.

## Caso base completo

1. Como administrador autorizado cree una solicitud con snapshot, responsable y fecha límite futura.
2. Márquela lista para enviar, genere el acceso externo y envíe la invitación.
3. Abra el enlace en una ventana privada. Confirme que solicita OTP y que un código inválido no autentica.
4. Ingrese el OTP. Confirme cookie `HttpOnly`, `SameSite=Lax` y respuestas `Cache-Control: no-store`.
5. Descargue la plantilla XLSX. No altere hojas ocultas, encabezados ni la firma.
6. Cargue la plantilla. Debe abrir **Vista previa del Excel** con conteos agregados. En este punto no debe existir una nueva `Respuesta`, `Cambio`, `Revisión`, versión ni evento de borrador.
7. Pulse **Cancelar**. Debe volver al portal sin crear borrador y el mismo token no debe reutilizarse.
8. Cargue nuevamente y pulse **Confirmar importación**. Solo ahora se crea una versión de borrador y su adjunto XLSX privado. Repetir el mismo POST no crea otra versión.
9. Revise el borrador y envíe definitivamente aceptando la declaración. Confirme acuse y cambio de estado.
10. Cargue un PDF/JPG/PNG de soporte y confirme que queda privado, asociado a la respuesta y pendiente de revisión antivirus; rechace extensiones dobles y archivos fuera del límite.
11. En la vista interna abra la respuesta y registre una decisión por cambio. Solicite corrección, regenere el acceso y confirme que llega el correo de acceso renovado sin reactivar un acceso anterior.
12. Complete la corrección externa, envíela y apruébela. Verifique que no sea posible aprobar con decisiones pendientes.
13. Descargue el comparativo y compruebe valores inicial/solicitado/aprobado, metadatos firmados y protección contra fórmulas.
14. Descargue el consolidado únicamente después de aprobación completa; antes debe rechazarse.
15. Revise la campana/buzón interno: invitación, respuesta, corrección, recordatorio y vencimiento no deben duplicarse.
16. Una falla secundaria nunca debe revelar detalles técnicos ni impedir consultar el expediente disponible.

## Casos negativos de Excel

- `.xlsm`, macro, objeto binario, vínculo externo, fórmula o hipervínculo: rechazo antes de crear preview.
- Encabezado, solicitud, ramo, revisión de snapshot o firma modificados: rechazo.
- Archivo fuera de límites, ZIP excesivo, referencia duplicada o fila ajena: rechazo.
- Token de otra sesión, solicitud o acceso: rechazo sin importar.
- Preview expirada o archivo cifrado alterado: rechazo sin borrador.
- Ceros y pagos negativos: solo conteos; nunca valores.

## Procesamiento programado

Simulación reproducible:

```powershell
python manage.py colectivos_process_deadlines --dry-run --limit 200 --now 2026-08-04T09:00:00-05:00
```

Procesamiento real local:

```powershell
python manage.py colectivos_process_deadlines --limit 200
```

Verifique accesos y OTP vencidos, previews eliminadas, solicitudes próximas a vencer, vencidas y avisos de cancelación. Ejecute una segunda vez: estados terminales no cambian y correos/notificaciones conservan su clave idempotente. Las sesiones externas son cookies firmadas sin estado servidor; expiran por edad al validarse y por ello `external_sessions_expired` permanece en cero.

Programe cada 15 minutos mediante el scheduler operativo, con exclusión mutua en el scheduler. `--limit` controla el lote. Supervise códigos saneados del proveedor, nunca destinatarios completos.

## QA responsive y seguridad

Revise verificación OTP, portal, preview y confirmación en 320, 375, 768, 1024 y 1440 px. Compruebe ausencia de scroll horizontal, tablas contenidas, foco visible y mensajes legibles. Confirme CSRF en todos los POST, ausencia de secretos/documentos en HTML, logs o URLs, archivos privados fuera de `STATIC_URL`, anti-IDOR y bloqueo de respuestas en estados terminales.

## Rollback operativo

- `COLECTIVOS_DEADLINES_ENABLED=false` suspende el procesador después de reiniciar.
- `COLECTIVOS_DEADLINE_EMAIL_ENABLED=false` suspende correos manteniendo el barrido de estados.
- No elimine migraciones ni edite la base. Las previews pendientes expiran y su archivo cifrado se elimina mediante el comando.

## Limpieza del expediente QA

1. Cancele o cierre el expediente desde la interfaz interna según su estado; nunca borre filas manualmente en producción.
2. Revoque accesos externos que continúen activos y ejecute el procesador de plazos.
3. Confirme que no quedan previews pendientes ni archivos temporales bajo `private_assets/colectivos/excel_previews`.
4. En una base local descartable puede eliminar el expediente mediante las herramientas administrativas aprobadas o restaurar la base de QA. No copie adjuntos ni respuestas a artefactos versionados.
5. Registre el resultado del QA sin documentos, nombres, destinatarios, tokens ni identificadores internos.

## Criterio de aceptación

Preview y confirmación están separadas; todos los POST requieren CSRF; confirmación es de uso único; cancelación/expiración no crean borradores; archivos temporales están cifrados; avisos son idempotentes; estados cerrados/cancelados quedan intactos y no existe escritura hacia Zoho.
