# Zoho CRM A&S — Discovery v2

## Estado

Discovery v2 implementado y probado durante este cambio exclusivamente con
mocks y snapshots sintéticos. La ejecución real anterior contra Sandbox
demostró que Fields puede estar disponible solo para parte de los módulos; esa
cobertura parcial ahora se conserva como un snapshot útil.

Los discoveries históricos bajo `docs/cotizacion_colectivos/` y
`artifacts/zoho/` son evidencia anterior, no la fuente de verdad vigente.

## Arquitectura

```text
Zoho CRM
  → integrations.zoho.get_zoho(profile explícito)
  → DiscoveryService (metadata read-only)
  → SnapshotStore
      ├── sandbox/latest + history
      └── production/latest + history
  → comparador local
  → comparison/*.json|md y MODEL.md
```

No existe cliente OAuth, TokenStore, backend o transporte adicional. El
discovery nunca consulta Records, Search API ni COQL.

## Comandos

Los siguientes comandos efectuarán lecturas reales únicamente cuando un
operador los ejecute conscientemente después de corregir el SDK:

```powershell
python manage.py zoho_discover --profile sandbox
python manage.py zoho_discover --profile production
python manage.py zoho_compare --left sandbox --right production
```

`--profile` es obligatorio. No se usa silenciosamente
`ZOHO_ACTIVE_PROFILE`. `zoho_compare` opera solo sobre archivos locales y no
inicializa la fachada.

## Esquema de snapshot

Cada `latest/` contiene:

- `manifest.json`: perfil, entorno confirmado, versión, backend, conteos,
  estado `success`/`partial`, digest semántico y timestamp principal.
- `organization.json`: identidad y configuración general no sensible.
- `modules.json`: inventario completo de módulos recibido.
- `fields.json`: campos normalizados y ordenados por módulo/API name.
- `layouts.json`: layouts cuando la fachada los expone.
- `relationships.json`: lookups demostrados por metadata, incluidos los no
  resueltos con una razón explícita.
- `related_lists.json`: related lists cuando la fachada las expone.
- `subforms.json`: campos subform y módulo interno si metadata lo demuestra.
- `picklists.json`: valores, estado, secuencia y dependencias disponibles.
- `errors.json`: fallos saneados por módulo y endpoint.

Cada módulo declara el estado de sus capacidades (`fields_status`,
`layouts_status` y `related_lists_status`). Un error solo conserva API name,
tipo de endpoint, categoría, código HTTP cuando existe y mensaje seguro.

Ningún archivo contiene registros CRM, tokens, secretos, headers o respuestas
HTTP crudas.

## Determinismo e históricos

Los JSON usan orden de claves y colecciones estable. El timestamp aparece solo
en el manifest. El estado y la cobertura técnica forman parte del digest: un
`partial` no equivale a un `success` con otra cobertura. Un snapshot
semánticamente idéntico no reemplaza `latest` ni crea otro histórico.

Antes de tocar `latest` o `history`, SnapshotStore valida el esquema mínimo,
escribe todos los JSON en un directorio temporal, vuelve a cargarlos y verifica
el digest. Solo entonces entra en la fase de publicación mediante renombres del
mismo filesystem, con restauración del `latest` previo si el reemplazo falla.
Un fallo durante construcción o validación deja ambos árboles intactos.

## Clasificación y mínimo publicable

- `fatal`: configuración/autenticación global, Organization inválida o no
  alineada con el perfil, Modules ausente/inválido o violación de aislamiento.
  No se llama a SnapshotStore.
- `partial`: fallo por módulo o capacidad (Fields 403/500 tras los retries del
  SDK, layouts/related lists no disponibles, lookup o subform no resoluble).
  Se publica el snapshot, se escribe `errors.json` y el comando termina con
  advertencias pero exit code normal.
- `success`: no quedó ningún error de metadata.

La condición mínima para publicar es Organization válida, entorno igual al
perfil explícito y una respuesta Modules válida y no vacía. Fields y las
capacidades opcionales pueden quedar parciales. La política de retries sigue
siendo exclusivamente la de `ays_zoho_sdk`; Discovery no agrega HTTP directo.

## Capacidades de la fachada

La versión actual confirma Organization, Modules y Fields. Layouts y related
lists se consumen únicamente si la fachada pública ofrece `list_layouts` y
`list_related_lists`. Si no existen, el snapshot registra la limitación. No se
importan backends ni se usa REST directo como atajo.

## Comparador

Detecta:

- módulos agregados, eliminados y modificados;
- campos agregados/eliminados y cambios de tipo, obligatoriedad, solo lectura
  o lookup;
- layouts agregados/eliminados/modificados;
- relaciones agregadas/eliminadas y cambios de destino;
- valores picklist agregados/eliminados, habilitados/deshabilitados y cambios
  de presentación.

La salida humana evita clasificar cambios puramente cosméticos como críticos.
Si una capacidad está marcada como no disponible para un módulo en cualquiera
de los lados, el comparador emite `comparison_inconclusive` con
`metadata_unavailable_left` o `metadata_unavailable_right`; no convierte esa
ausencia en campos, relaciones, picklists o layouts agregados/eliminados.

## Seguridad

- Solo metadata y operaciones READ.
- Perfiles `sandbox` y `production` explícitos y aislados.
- Validación cerrada `Organization.environment == profile`.
- Fallos parciales no abortan los demás módulos.
- Sin modelos ni migraciones.
- Sin acceso a registros, PII o CardManager.
- Sin modificación de OAuth, scopes, SDK, `.env` o credenciales.

## Primer uso pendiente

1. Actualizar `ays_zoho_sdk` cuando el mantenedor publique la corrección.
2. Autorizar nuevamente Sandbox.
3. Validar la conexión de Sandbox.
4. Ejecutar `zoho_discover --profile sandbox`.
5. Revisar el snapshot.
6. Habilitar/validar Producción READ.
7. Ejecutar `zoho_discover --profile production`.
8. Ejecutar `zoho_compare --left sandbox --right production`.
9. Revisar `MODEL.md` antes de cualquier diseño de escritura.
