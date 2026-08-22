# Cotizacion - Colectivos: fase de descubrimiento

Esta fase no implementa buscadores ni vistas funcionales. Los metadatos locales
del Sandbox confirman que `Persona_juridica` y `Accounts` no entregaron campos
por permisos, por lo que no existe evidencia suficiente para definir empresas,
NIT, relaciones ni un mapeo completo sin inventar nombres API.

## Arquitectura preparada

```text
Cotizacion - Colectivos
        -> comandos de descubrimiento controlado
        -> get_zoho(profile="sandbox")
        -> integrations/zoho
        -> Zoho CRM Sandbox (solo lectura)
```

No existen modelos, migraciones, URLs, vistas, persistencia local ni operaciones
de escritura en esta fase.

## Descubrir metadatos

```powershell
python manage.py colectivos_discover_schema --profile sandbox
```

Genera exclusivamente metadatos en `artifacts/zoho/colectivos/`:

- `modules.json`
- `fields.json`
- `relationships.json`
- `search_candidates.json`
- `discovery.md`

Los candidatos se marcan como pendientes; el comando no los convierte en una
configuracion funcional automáticamente. No consulta Production ni registros.

## Muestreo manual posterior

Solo después de revisar el reporte y confirmar módulo y campos:

```powershell
python manage.py colectivos_probe_data `
  --profile sandbox `
  --module <MODULO_CONFIRMADO> `
  --fields <CAMPO_1> <CAMPO_2> `
  --limit 3 `
  --allow-real-read
```

El comando rechaza Production, exige confirmación explícita, valida módulo y
campos contra metadata, limita la muestra a diez registros y solo imprime tipo
y longitud de los valores. No imprime documentos, nombres, correos, teléfonos,
pólizas, IDs ni respuestas JSON, y no genera archivos con registros.

Las ejecuciones reales deben ser explícitas, pequeñas, únicamente contra Sandbox
y quedar contabilizadas. Nunca deben usar HTTP directo para evitar la fachada.

## Hallazgo dirigido: Fonconstruimos

El snapshot y las lecturas dirigidas distinguen dos hechos diferentes:

- `Tasks.Vendedor` contiene Fonconstruimos como valor utilizado; esto no modela
  el fondo ni sus empresas;
- tres `Riesgos1` tienen el texto exacto `Tomador = Fonconstruimos`; uno enlaza
  un `Asegurado`, pero no fue posible leer su Contact por un `SDKException`.

Las búsquedas exactas en `Contacts.Grupo_econ_mico`, `Contacts.Empresa`, los
tres lookups candidatos de `Polizas`, `Polizas.Vendedor`, `Riesgos1.Subgrupo`
y `Tasks.Vendedor` no devolvieron registros. `Accounts` continúa inconcluso:
metadata devolvió `authorization` y records devolvió categoría `sdk`.

Por tanto no existe evidencia suficiente para construir la lista válida
Fonconstruimos → empresas. Antes de implementar el selector se debe inspeccionar
en Sandbox una empresa conocida del fondo y el Contact enlazado por el único
`Riesgos1.Asegurado`, revisando `Empresa`, `Grupo_econ_mico` y related lists. El
backend deberá validar la pertenencia; una lista hardcodeada o un ID del navegador
no son evidencia válida.

## Condición para continuar

Camilo debe revisar `discovery.md` y confirmar como mínimo:

1. módulo de empresas;
2. módulo de individuos;
3. campo NIT;
4. campo de documento individual;
5. campos de nombre;
6. relaciones demostrables con pólizas, asegurados y riesgos.

Hasta tener esa evidencia, no se habilitan búsquedas, detalle, relaciones ni un
enlace en el Banco de Herramientas.
