# Tres herramientas sobre un motor común de Colectivos

## Estado

Implementado en la interfaz web. El nombre técnico de la aplicación Django se
mantiene como `cotizacion_colectivos` para preservar imports, migraciones,
tablas, tokens, Workspace y compatibilidad.

Las entradas funcionales son **Novedades**, **Invitaciones a
Aseguradoras** y **Cotización Individual**. Las tres comienzan en el buscador
común y reutilizan cliente, póliza, Workspace, Snapshot y perfil Zoho.

## Arquitectura

```text
Banco de Herramientas
├── Novedades (Ingreso y Retiro)
├── Invitaciones a Aseguradoras
└── Cotización Individual
             │
             ▼
      Motor común de Colectivos
      ├── búsqueda de cliente
      ├── ficha consolidada
      ├── pólizas
      ├── Policy Workspace / Snapshot
      ├── enlaces y respuestas
      ├── catálogo de invitaciones
      └── catálogo de formularios por ramo
```

No existen dos aplicaciones, dos clientes Zoho ni dos Workspaces para una
misma póliza. El modo de entrada es contexto de presentación; no forma parte de
la identidad del Workspace y no altera el perfil Zoho.

## Puntos de entrada

- `/cotizacion-colectivos/novedades/`
- `/cotizacion-colectivos/invitaciones-aseguradoras/`
- `/cotizacion-colectivos/cotizacion-individual/`

El modo se fija mediante rutas declaradas en servidor y se conserva en sesión
con una allowlist cerrada (`novelties`, `invitations`, `individual`). No se acepta un modo
libre desde formulario, query string, cookie personalizada o identificador CRM.

Las rutas históricas se conservan para compatibilidad. La entrada histórica
usa por defecto Novedades. La ruta anterior de solicitudes/renovaciones es
compatibilidad legacy y no se enlaza desde el Banco.

## Buscador común

Cada herramienta muestra un solo campo: **Buscar por nombre o identificación**.
La capa de negocio compone las búsquedas confirmadas de empresa y persona sobre
un único facade ya validado. Esto evita repetir la validación de Organization.

- Entrada numérica: documento exacto y, si no hay coincidencia, prefijo.
- Entrada textual: nombre comercial, razón social o nombre de persona.
- Máximo global: 20 resultados.
- Los resultados se presentan como clientes y conservan internamente el tipo
  firmado `company` o `person`.
- No se consultan relaciones para mostrar el listado, evitando N+1.

La ficha consolidada sí muestra las pólizas relacionadas mediante los servicios
existentes. Los tokens siguen siendo opacos, firmados y ligados al tipo.

## Comportamiento por herramienta

### Novedades

La acción primaria es generar un enlace contextualizado para capturar solo un
Ingreso o Retiro. Se mantienen expiración, regeneración, revocación,
miniportal, respuesta local y notificación. Renovaciones está fuera de alcance.

### Invitaciones a Aseguradoras

La acción primaria es abrir el preview y descargar las plantillas activas del
ramo de la póliza. El ramo se lee del Workspace local: no se vuelve a consultar
Zoho ni se intenta inferirlo.

La aseguradora vigente de la póliza no filtra el catálogo. Para un ramo se
ofrecen todas las plantillas activas registradas. Una plantilla se incorpora de
forma declarativa con ramo, aseguradora, versión, estado, archivo y mapping; el
generador no contiene una bifurcación por aseguradora.

Estado inicial del catálogo:

- Movilidad colectivo: SURA y Allianz activas; la descarga conjunta es ZIP.
- Vida grupo deudores: Allianz XLSX activa; SURA BIFF8 `.xls` catalogada e
  inactiva hasta disponer de edición que preserve verificablemente el formato
  maestro.

### Cotización Individual

El usuario busca una empresa o persona, abre una póliza y selecciona un afiliado
confirmado por referencia HMAC. El ramo de la póliza determina el esquema
declarativo; no se acepta selección libre desde el navegador. El enlace externo
contiene contexto opaco, firmado, cifrado y temporal. La respuesta se persiste
localmente cifrada, genera una notificación simple y no crea registros ni tareas
en Zoho. Con Workspace vigente, ficha, enlace y formulario externo son locales.

## Persona y empresa

Ambos recorridos aceptan empresas y personas. La misma ficha de cliente y la
misma ficha de póliza se reutilizan. La UI habla de “cliente”; el tipo técnico
solo determina la validación del token y el servicio confirmado que resuelve el
registro.

## Seguridad y límites

- Zoho continúa en solo lectura.
- No se crean tareas ni se actualizan registros CRM.
- El modo no modifica HMAC, tokens, anti-IDOR, CSRF, cifrado ni no-store.
- Los documentos permanecen enmascarados en resultados.
- El catálogo y las plantillas se procesan desde Workspace/Snapshot local.
- La escritura futura en Zoho está fuera de alcance.

## QA operativo

Verificar desde el Banco los tres accesos, la búsqueda de empresa y persona, la
navegación cliente → póliza y el énfasis de acciones. En Solicitudes validar
generación/apertura del enlace; en Invitaciones validar preview, XLSX y ZIP;
en Cotización Individual validar cada ramo, repetibles, adjuntos y confirmación.
Las lecturas reales deben realizarse únicamente con autorización operativa y el
perfil global configurado.
