# Miniportal externo y Excel de ida y vuelta

> Documento histórico. El flujo funcional vigente es el enlace directo sin
> OTP descrito en `direct_external_flow.md`; las referencias a OTP se conservan
> únicamente para explicar datos y componentes heredados.

## Alcance

El miniportal amplía el expediente local `SolicitudColectivo`; no crea otro sistema de solicitudes y no escribe en Zoho. El cliente opera contra el snapshot cifrado. La ruta pública queda aislada bajo `/solicitudes/colectivos/externa/` y no crea un `User`, una sesión de Vault ni una autenticación MFA interna.

## Flujo

1. A&S deja el expediente en `LISTA_PARA_ENVIAR`, confirma destinatario, fecha límite, snapshot y tratamiento de datos.
2. Se genera un selector aleatorio y un secreto. Solo se persiste SHA-256 del secreto; el token completo vive durante el envío del correo.
3. El enlace expira independientemente de la fecha límite del expediente. Regenerarlo revoca los accesos anteriores sin alterar el snapshot.
4. En producción se exige OTP por correo. Se persiste únicamente un hash, tiene expiración y límite de intentos.
5. La verificación crea una cookie firmada, `HttpOnly`, limitada a una solicitud y a la ruta externa. No inicia sesión Django.
6. El cliente guarda versiones de borrador y finalmente envía. La sesión y el acceso se invalidan y el expediente pasa a `RESPONDIDA`.
7. Web y XLSX convergen en `RespuestaSolicitudColectivo` y `CambioSolicitudColectivo`.
8. A&S registra decisiones inmutables por cambio. Una corrección genera un acceso nuevo sobre el mismo expediente; una aprobación permite exportar el consolidado, pero nunca escribe en Zoho.

## Ramos

Salud colectivo (`91`) tiene formulario web editable para confirmar, modificar, retirar e incluir. Exequial (`86`), Hogar (`28`), Vida grupo deudores (`83`) y Movilidad (`40`) conservan presentación y Excel controlado, sin habilitar campos específicos no confirmados.

## Excel

La plantilla contiene `Novedades`, `Póliza`, `Instrucciones`, `Catálogos` y `Metadatos` oculta. La metadata está firmada y vinculada a solicitud, ramo, revisión del snapshot y nonce. El importador acepta exclusivamente XLSX, limita tamaño/filas/descompresión, rechaza macros, binarios, relaciones externas, fórmulas, hipervínculos y encabezados alterados. La importación crea un borrador `EXCEL`; no lo envía automáticamente.

Las exportaciones son independientes:

- plantilla de novedades;
- respuesta recibida;
- comparativo con decisiones;
- consolidado, solo cuando todas las decisiones están aprobadas.

Los archivos `Novedades Junio_Fonconstruimos.xlsx` y `MT-CA-01 Matriz de Ramos (1).xlsx` son fuentes funcionales de solo lectura. No se sobrescriben ni se copian a los outputs.

## Adjuntos

Se admiten PDF, JPG/JPEG y PNG; XLSX solo en el canal Excel. Se valida firma de contenido, extensión, MIME, tamaño individual y total, doble extensión, macros y rutas. Se almacena con nombre aleatorio fuera de estáticos, checksum y descarga interna con permiso y `no-store`. El estado inicial es `REVISION_ANTIVIRUS`: no existe un motor antivirus configurado y no se afirma lo contrario.

## Configuración

- `COLECTIVOS_EXTERNAL_ACCESS_VERIFICATION=otp_email` (`token_only` únicamente con `DEBUG=true`).
- `COLECTIVOS_EXTERNAL_LINK_TTL_SECONDS=86400`.
- `COLECTIVOS_EXTERNAL_LINK_MAX_TTL_SECONDS=604800`.
- `COLECTIVOS_EXTERNAL_OTP_TTL_SECONDS=600`.
- `COLECTIVOS_EXTERNAL_OTP_MAX_ATTEMPTS=5`.
- `COLECTIVOS_EXTERNAL_SESSION_TTL_SECONDS=1800`.
- `COLECTIVOS_ATTACHMENT_MAX_BYTES=10485760`.
- `COLECTIVOS_ATTACHMENT_TOTAL_BYTES=26214400`.
- `COLECTIVOS_EXTERNAL_BASE_URL=https://host-autorizado`.

## Retención

No se elimina automáticamente información operativa hasta que A&S apruebe una política. Los OTP dejan de ser utilizables al vencer o usarse; las sesiones vencen por firma/TTL; los accesos se conservan como evidencia técnica sin secreto en claro; respuestas, revisiones, eventos y adjuntos se conservan como expediente. Una tarea futura deberá definir plazos, bloqueo legal y eliminación segura.

## QA manual

Aplicar migraciones en un entorno QA, usar destinatario técnico y datos no reales. Crear una solicitud Salud, generar enlace, validar OTP, guardar modificación/inclusión/retiro, adjuntar archivo, descargar/cargar plantilla, revisar el preview, enviar, revisar internamente, solicitar corrección, responder, aprobar y descargar comparativo/consolidado. Verificar 320, 375, 768, 1024 y 1440 px, ausencia de scroll horizontal, CSRF, cookies y respuestas `no-store`.

No enviar correos a clientes reales ni ejecutar escrituras contra Zoho durante QA.
