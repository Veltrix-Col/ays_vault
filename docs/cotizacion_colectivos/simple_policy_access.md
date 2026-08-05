# Acceso simple desde la póliza

## Recorrido principal

El único recorrido operativo es:

```text
Buscar empresa o individuo
  -> abrir póliza
  -> Generar enlace
  -> Copiar enlace
  -> cliente abre y responde
  -> A&S recibe una notificación informativa
```

La póliza permite elegir únicamente **Actualización** o **Renovación**. No pide
motivo, responsable, destinatario, mensaje, confirmación de snapshot, OTP,
revisión ni aprobación. Los modelos históricos conservan snapshot,
trazabilidad y respuesta, pero no son trabajo administrativo visible.

## Resolución automática de estados

- Sin enlace: crea la estructura local mínima y presenta el enlace.
- Enlace vigente cuyo secreto ya no se puede recuperar: **Generar enlace**
  revoca el anterior y presenta uno nuevo.
- Revocado o vencido: genera un reemplazo inmediatamente.
- Respondido: conserva el ciclo anterior y crea una versión nueva sin revisión
  ni cierre previo.
- Doble envío accidental: el lock vuelve a comprobar la estructura exacta de
  la póliza y evita duplicarla.

Un acceso multipóliza nunca se reutiliza para el acceso directo de una sola
póliza. La comparación exige que el conjunto de referencias sea exactamente el
de la póliza abierta. Las acciones usan siempre el token firmado de la ruta
actual; un token histórico restaurado desde el snapshot nunca se reutiliza.

## Volumen y datos locales

`COLECTIVOS_GROUP_PAGE_SIZE` controla páginas de hasta 200 registros y
`COLECTIVOS_GROUP_MAX_RECORDS` tiene un máximo defensivo predeterminado de
10.000. La lectura pagina hasta completar el grupo. Si Zoho anuncia más datos
después del máximo, la hidratación falla cerrada antes de publicar un snapshot;
nunca se genera un miniportal parcial.

Con Workspace vigente, ficha, grupo, Excel, generación/revocación de acceso,
miniportal, respuesta y notificación trabajan localmente. Solo **Actualizar
información desde Zoho** o la expiración del Workspace vuelven a hidratar.

## QA manual

1. Abrir una póliza y generar/copiar el enlace.
2. Abrirlo sin sesión interna; debe responder 200 y no pedir OTP.
3. Confirmar o enviar observaciones; debe aparecer una única respuesta
   informativa en A&S.
4. Generar otro enlace después de responder.
5. Revocarlo, comprobar 410 en el anterior y generar uno nuevo.
6. Confirmar en logs `cache=hit` y `remote_queries=0` en operaciones locales.

