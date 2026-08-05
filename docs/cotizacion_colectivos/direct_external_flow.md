# Flujo externo directo de Colectivos

## Contrato funcional

El enlace se genera en el Workspace de una póliza confirmada y se muestra en
la misma respuesta, con acciones **Copiar**, **Abrir**, **Generar otro** y
**Revocar**. Compartirlo por correo u otro canal ocurre fuera de la plataforma.
No existe OTP, login ni pantalla intermedia para el cliente.

El cliente revisa la información del snapshot local, confirma o escribe
observaciones y finaliza. La respuesta crea una notificación
`CLIENT_RESPONSE`; no crea asignación, aprobación, revisión o tarea. La página
interna **Respuestas** filtra exclusivamente esas notificaciones, incluso si la
base conserva avisos administrativos históricos.

## Seguridad

- El secreto del enlace es aleatorio; solo se persiste su hash SHA-256 y se
  compara en tiempo constante.
- El acceso se vincula a una estructura local y a su snapshot cifrado.
- Las sesiones externas son firmadas, `HttpOnly`, `SameSite=Lax` y limitadas a
  la ruta del portal.
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
COLECTIVOS_EXTERNAL_LINK_DAYS=15
COLECTIVOS_EXTERNAL_LINK_MAX_DAYS=30
COLECTIVOS_GROUP_PAGE_SIZE=200
COLECTIVOS_GROUP_MAX_RECORDS=10000
```

