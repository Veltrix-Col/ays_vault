# Zoho CRM A&S — Discovery v2

## Estado

Discovery v2 implementado y probado exclusivamente con mocks y snapshots
sintéticos. No se ha ejecutado contra Sandbox ni Producción debido al defecto
conocido de validación OAuth Sandbox en la versión instalada de
`ays_zoho_sdk`.

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
  digest semántico y timestamp principal.
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

Ningún archivo contiene registros CRM, tokens, secretos, headers o respuestas
HTTP crudas.

## Determinismo e históricos

Los JSON usan orden de claves y colecciones estable. El timestamp aparece solo
en el manifest. Un snapshot semánticamente idéntico no reemplaza `latest` ni
crea otro histórico. Cuando cambia, el `latest` anterior se mueve a
`history/<timestamp>/` antes de publicar el nuevo.

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
