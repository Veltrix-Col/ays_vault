# Accesos externos, OTP y expediente local

## Contrato vigente

El enlace externo amplía un expediente local; no crea usuarios, no inicia una
sesión interna de Vault y no escribe en Zoho. Las rutas públicas están aisladas
bajo `/solicitudes/colectivos/externa/`.

1. A&S selecciona o corrige el correo destinatario y genera el acceso.
2. Se crea selector + secreto aleatorios; sólo se persiste el hash del secreto.
3. El acceso vence exactamente en `created_at + 172800 segundos` por defecto.
   Una fecha límite anterior puede acortarlo, nunca alargarlo.
4. Abrir el token no autoriza el formulario: emite un OTP al correo registrado.
5. Sólo se guarda el hash del OTP. El código tiene vencimiento, máximo de
   intentos y regeneración controlada; un OTP vigente no se reenvía por cada GET.
6. Verificarlo crea una cookie firmada, `HttpOnly`, limitada al acceso y a la
   ruta externa. El correo se presenta enmascarado.
7. Al enviar se registra la respuesta, se consume la sesión/acceso y se crea una
   alerta local idempotente.

Novedades reutiliza `services/external.py`. Cotización Individual emplea el
mismo contrato de seguridad y configuración mediante `services/individual_access.py`,
con su propio modelo/cookie para no mezclar expedientes.

El correo OTP es multipart (`text/plain` y `text/html`) y usa HTML compatible
con Outlook sin CSS externo. La capa común conserva la redacción automática de
secuencias sensibles; sólo los tipos cerrados `COLECTIVOS_OTP` y
`COLECTIVOS_INDIVIDUAL_OTP` pueden entregar el bloque de seis dígitos al backend
de correo. Esa excepción no persiste el cuerpo ni el código, no lo registra en
logs o auditoría y rechaza cualquier tipo de notificación distinto.

## Contenido y archivos

El cliente opera contra Snapshot cifrado. Web y XLSX convergen en respuestas y
cambios locales. Los archivos se validan por extensión, MIME, magic bytes,
tamaño y límites agregados; se almacenan cifrados fuera de estáticos y se sirven
internamente con permiso y `no-store`.

No existe antivirus configurado, por lo que el estado inicial sigue siendo
`REVISION_ANTIVIRUS`. Tampoco existe una API pública confirmada de attachments
en `ays-zoho-sdk` 1.1.0: no se usa HTTP directo para subirlos.

## Configuración

- `COLECTIVOS_EXTERNAL_LINK_TTL_SECONDS=172800`.
- `COLECTIVOS_EXTERNAL_LINK_MAX_TTL_SECONDS=604800`.
- `COLECTIVOS_EXTERNAL_OTP_TTL_SECONDS=600`.
- `COLECTIVOS_EXTERNAL_OTP_MAX_ATTEMPTS=5`.
- `COLECTIVOS_EXTERNAL_SESSION_TTL_SECONDS=1800`.
- `COLECTIVOS_ATTACHMENT_MAX_BYTES=10485760`.
- `COLECTIVOS_ATTACHMENT_TOTAL_BYTES=26214400`.
- `COLECTIVOS_EXTERNAL_BASE_URL=https://host-autorizado`.

Las variables heredadas expresadas en días se conservan sólo por compatibilidad
con flujos legacy; los accesos aquí descritos usan segundos y no redondean.

## Retención y QA

No se elimina automáticamente información operativa hasta que A&S apruebe una
política. Los OTP y sesiones dejan de ser utilizables al vencer o consumirse;
accesos, respuestas, eventos y archivos se conservan como evidencia sin secretos
en claro.

En QA se deben usar destinatarios técnicos y datos no reales. Verificar token sin
OTP, código incorrecto, máximo de intentos, expiración, reenvío controlado,
consumo, CSRF, cookies, `no-store` y 320/375/768/1024/1440 px. No enviar a
clientes reales ni ejecutar escrituras Zoho.
