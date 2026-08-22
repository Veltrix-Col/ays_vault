# Flujo externo protegido de Colectivos

## Contrato funcional

El enlace se genera en el Workspace de una póliza confirmada para un correo
destinatario editable. Se muestra en la misma respuesta, con acciones **Copiar**,
**Abrir**, **Generar otro** y **Revocar**. El token no concede acceso directo:
primero se emite y verifica un OTP enviado al destinatario registrado.

El cliente revisa la información del snapshot local, confirma o escribe
observaciones y finaliza. La respuesta crea una notificación
`CLIENT_RESPONSE`; no crea asignación, aprobación, revisión o tarea. La página
interna **Respuestas** filtra exclusivamente esas notificaciones, incluso si la
base conserva avisos administrativos históricos.

## Seguridad

- El secreto del enlace es aleatorio; solo se persiste su hash SHA-256 y se
  compara en tiempo constante.
- El acceso se vincula a una estructura local y a su snapshot cifrado.
- El OTP se almacena sólo como hash, vence, limita intentos y no se reenvía
  mientras exista otro vigente.
- Tras verificarlo, la sesión externa es firmada, `HttpOnly`, `SameSite=Lax` y
  limitada al acceso y a la ruta del portal.
- Los POST conservan CSRF; las respuestas usan `no-store`.
- Un enlace revocado responde 410 y el reemplazo tiene un secreto distinto.
- No se registran tokens, documentos, nombres, cuerpos ni IDs de Zoho.
- El `correlation_id` del portal proviene del request HTTP saneado o de un UUID
  técnico efímero; nunca de un atributo inexistente del modelo.

## Persistencia y rendimiento

El portal restaura el snapshot persistido. No inicializa la fachada Zoho ni
consulta Contacts, Polizas, Riesgos1 o Riesgos. La generación directa reutiliza
la preparación exacta de la póliza y tampoco consulta Zoho con Workspace
vigente.

Configuración relacionada:

```env
COLECTIVOS_EXTERNAL_LINK_TTL_SECONDS=172800
COLECTIVOS_EXTERNAL_LINK_MAX_TTL_SECONDS=604800
COLECTIVOS_EXTERNAL_OTP_TTL_SECONDS=600
COLECTIVOS_EXTERNAL_OTP_MAX_ATTEMPTS=5
COLECTIVOS_GROUP_PAGE_SIZE=200
COLECTIVOS_GROUP_MAX_RECORDS=10000
```
