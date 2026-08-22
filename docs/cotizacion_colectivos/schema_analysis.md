# Análisis del esquema Sandbox para Cotización – Colectivos

## 1. Resumen ejecutivo

Este informe analiza exclusivamente los artefactos locales generados el
2026-08-01 por `colectivos_discover_schema`. Los encabezados declaran
`profile=sandbox`, `environment=sandbox` y `content=metadata_only`. No se
consultaron registros ni Producción para elaborar este documento.

La evidencia más relevante es:

- `Contacts` es el candidato operativo principal tanto para empresas como para
  individuos. La metadata lo etiqueta como **Personas**, pero contiene el
  picklist `Tipo_de_persona` con valores `Persona natural` y `Persona jurídica`.
- `Contacts.Tipo_ID` incluye `CC`, otros documentos personales y `NIT`.
- `Contacts.N_mero_de_ID` es el único campo accesible cuya etiqueta es
  **Número ID**. Es candidato fuerte para documento/NIT, pero su uso y formato
  deben validarse con una muestra controlada.
- `Contacts` contiene los campos personales `First_Name`, `Last_Name` y
  `Full_Name`, y los corporativos `Raz_n_social` y `Nombre_comercial`.
- `Persona_juridica` es un candidato semántico importante porque su etiqueta
  singular es **Empresa**, pero Fields API respondió `authorization`; no puede
  mapearse ni utilizarse todavía.
- `Polizas`, `Riesgos` y `Riesgos1` están disponibles. `Riesgos1` tiene etiqueta
  **Asegurados** y lookups llamados `P_liza`, `Asegurado` y `Riesgo`.
- `relationships.json` contiene cero relaciones. Los campos de tipo `lookup` y
  `subform` existen, pero sus objetos `lookup` y `related_details` están vacíos.
  Por tanto, el destino de los lookups no está demostrado.

Conclusión: hay evidencia suficiente para diseñar una hipótesis de los dos
buscadores sobre `Contacts`, pero todavía no para implementarlos con seguridad.
Primero se debe comprobar población, formato de documentos, discriminación por
`Tipo_de_persona` y destinos de relaciones mediante validación funcional mínima.

## 2. Fuentes revisadas

- `artifacts/zoho/colectivos/modules.json`
- `artifacts/zoho/colectivos/fields.json`
- `artifacts/zoho/colectivos/relationships.json`
- `artifacts/zoho/colectivos/search_candidates.json`
- `artifacts/zoho/colectivos/discovery.md`
- `cotizacion_colectivos/discovery.py`
- `cotizacion_colectivos/management/commands/colectivos_discover_schema.py`
- `cotizacion_colectivos/probe.py`
- `cotizacion_colectivos/management/commands/colectivos_probe_data.py`

Resultado de integridad lógica de los artefactos:

- 20 módulos presentes en el reporte.
- 17 módulos con respuesta de Fields API.
- 3 fallos de autorización: `Accounts`, `Persona_juridica`, `Vendedores`.
- 2 módulos solicitados ausentes: `Coberturas_Asegurado`, `C_digos_Ramos`.
- 0 relaciones con destino serializado.
- Ningún registro de negocio incluido.

### Limitaciones del generador automático

1. `build_search_candidates()` usa coincidencias léxicas. Por eso omitió
   `Contacts.N_mero_de_ID` y produjo falsos positivos como
   `Fecha_de_expedici_n_de_documento`.
2. `build_relationships()` solo exporta campos si `lookup` o
   `related_details` contienen datos. En este reporte ambos están vacíos, aunque
   hay campos cuyo `data_type` es `lookup` o `subform`.
3. Todos los campos de todos los módulos aparecen con `unique=true`. Este valor
   no es creíble como indicador de unicidad funcional y no debe usarse para
   diseñar búsquedas o restricciones hasta validarlo.
4. `custom_module=false` tampoco distingue correctamente los módulos
   personalizados; para este análisis se utiliza `generated_type=custom`.

## 3. Inventario de módulos relevantes

| Label | API name | Tipo según metadata | Estado | Campos | Evaluación |
|---|---|---|---|---:|---|
| Cuentas | `Accounts` | estándar | `user_hidden`, API soportada | no disponibles | Candidato débil para empresas; bloqueado por autorización. |
| Personas | `Contacts` | estándar | `visible`, API soportada | 108 | Candidato fuerte para empresas e individuos. |
| Persona jurídica | `Persona_juridica` | personalizado | `user_hidden`, API soportada | no disponibles | Candidato semántico fuerte para empresas; no utilizable sin Fields API. |
| Pólizas | `Polizas` | personalizado | `visible`, API soportada | 167 | Confirmado por metadata como módulo de pólizas. |
| Riesgos | `Riesgos` | personalizado | `visible`, API soportada | 79 | Candidato fuerte para riesgos. |
| Asegurados | `Riesgos1` | personalizado | `visible`, API soportada | 84 | Candidato fuerte para asegurados/subriesgos. |
| Asegurados Colectivas | `Asegurados_Colectivas` | enlace web | `visible`, API no soportada | 0 | No es módulo de datos consultable. |
| Asegurados Greenland | `Asegurados_Greenland` | enlace web | `visible`, API no soportada | 0 | No es módulo de datos consultable. |
| Relacionado Mascota | `Relacionados` | subformulario | `visible`, API soportada | 5 | Subformulario asociado por nombre a `Empleados.Relacionados`. |
| Relacionado Hijos | `Relacionados_Hijoss` | subformulario | `visible`, API soportada | 5 | Subformulario asociado por nombre a `Empleados.Relacionados_Hijoss`. |
| Relaciones | `Relaciones` | subformulario | `visible`, API soportada | 6 | Subformulario asociado por nombre a `Contacts.Relaciones`. |
| Operaciones | `Opeeraciones` | personalizado | `visible`, API soportada | 117 | Contiene lookups de póliza y tomador; destinos no informados. |
| Renovaciones/Vencimientos | `Renovaciones_Vencimientos` | enlace web | `visible`, API no soportada | 0 | No es módulo de datos consultable. |
| Directorio Aseguradoras | `Directorio_Aseguradoras` | personalizado | `visible`, API soportada | 23 | Directorio de aseguradoras; no existe lookup confirmado desde pólizas. |
| Coberturas | `Ramo_12` | subformulario | `visible`, API soportada | 10 | Asociación estructural fuerte con `Polizas.Ramo_12`. |
| Tipo de Contacto | `Tipo_d_Contacto` | subformulario | `visible`, API soportada | 9 | Asociación estructural con `Directorio_Aseguradoras.Tipo_d_Contacto`. |
| Sucursales | `Informaci_n_de_contacto` | subformulario | `visible`, API soportada | 8 | Padre no identificado en la metadata exportada. |
| Familiar y contactos | `Informaci_n_familiar_y_co` | subformulario | `visible`, API soportada | 6 | Padre no identificado; contiene lookup `Personas`. |
| Empleados | `Empleados` | personalizado | `visible`, API soportada | 65 | Empleados internos; no debe confundirse con clientes. |

## 4. Campos de identidad y contacto por módulo

| Módulo | Documento | Nombre/razón social | Correo | Teléfono | Estado | Confianza y ambigüedad |
|---|---|---|---|---|---|---|
| `Contacts` | `Tipo_ID`, `N_mero_de_ID`; `Fecha_de_expedici_n_de_documento` es solo fecha | `First_Name`, `Last_Name`, `Full_Name`, `Raz_n_social`, `Nombre_comercial` | `Email`, `Secondary_Email`, `Otro_correo_electr_nico` | `Phone`, `Mobile`, `Other_Phone`, `Tel_fono1/2/3` | `Estado`; `Record_Status__s` es técnico | Alta para estructura; población real pendiente. |
| `Persona_juridica` | No disponible | No disponible | No disponible | No disponible | No disponible | No determinado por `authorization`. |
| `Accounts` | No disponible | No disponible | No disponible | No disponible | No disponible | No determinado por `authorization`. |
| `Polizas` | `N_mero_de_certificado` no es documento de persona; `NIT_Aseguradora` pertenece a aseguradora | `Name` es póliza; `Raz_n_social` es texto desnormalizado | `Email`, correos comercial/facturación/cartera | No identificado | `Estado_de_la_p_liza`, `Activar_p_liza` | No usar como fuente maestra de empresa/persona. |
| `Riesgos` | Ninguno identificado | `Name` es key de riesgo; otros nombres dependen del tipo de riesgo | `Email` y correos alternos | Ninguno identificado | `Record_Status__s` | No es módulo de identidad principal. |
| `Riesgos1` | Ninguno identificado | `Name` es subriesgo; `Nombre_riesgo` | `Email` y correos de beneficiario/afiliado | Ninguno identificado | `Estado`, `Record_Status__s` | Candidato de asegurados, no maestro de identidad. |
| `Asegurados_Colectivas` | Sin campos | Sin campos | Sin campos | Sin campos | Sin campos | Enlace web, no módulo API. |
| `Asegurados_Greenland` | Sin campos | Sin campos | Sin campos | Sin campos | Sin campos | Enlace web, no módulo API. |
| `Relacionados` | Ninguno | `Nombre` | Ninguno | Ninguno | Ninguno | Subformulario; datos limitados. |
| `Relacionados_Hijoss` | Ninguno | `L_nea_nica_1` | Ninguno | Ninguno | Ninguno | Subformulario; datos limitados. |
| `Relaciones` | Ninguno | `Nombre_del_cliente` es lookup | Ninguno | Ninguno | Ninguno | Destino del lookup no informado. |
| `Opeeraciones` | `N_mero_de_certificado` es certificado, no identidad | `Name` es operación | `Email`, `Correo_facturaci_n` | Ninguno identificado | estados de cartera/pago y `Record_Status__s` | No es maestro de identidad. |
| `Renovaciones_Vencimientos` | Sin campos | Sin campos | Sin campos | Sin campos | Sin campos | Enlace web, no módulo API. |
| `Directorio_Aseguradoras` | Ninguno | `Nombre_Aseguradora` | Contactos están en subformulario | `L_nea_Asistencia` | `Estado_Aseguradora` | Directorio; contiene campos tipo credencial que jamás deben consultarse. |
| `Ramo_12` | Ninguno | Ninguno | Ninguno | Ninguno | Ninguno | Subformulario de coberturas. |
| `Tipo_d_Contacto` | Ninguno | `Nombre_Completo1` | `Correo_electr_nico` | `Tel_fono`, `Celular` | Ninguno | Contacto subordinado; padre por determinar. |
| `Informaci_n_de_contacto` | Ninguno | `Ciudad` tiene label “Nombre de la sede” | Ninguno | `Tel_fono1`; `Tel_fono` tiene label “Barrio” pese al tipo phone | Ninguno | Metadata semánticamente inconsistente; requiere validación. |
| `Informaci_n_familiar_y_co` | Ninguno | `Personas` es lookup | Ninguno | Ninguno | Ninguno | Padre y destino del lookup no informados. |
| `Empleados` | `N_mero_ID` | `Name`; `Nombre` es contacto de emergencia | `Email`, `Correo_electr_nico_personal` | `Celular`, teléfonos de emergencia | `Estado` | Módulo de empleados A&S, no de clientes. |

## 5. Campos de negocio y relaciones por módulo

| Módulo | Lookups/subformularios relevantes | Póliza | Aseguradora/Ramo | Vigencia | Evaluación |
|---|---|---|---|---|---|
| `Contacts` | `Empresa`, `Representante_legsal`, `Grupo_econ_mico`, `Relaciones` (subform) | Sin relación directa confirmada | Solo campos copiados “en BD aseguradora” | `fecha_de_renovaci_n` | Los destinos de lookup no están serializados. |
| `Polizas` | `Tomador_principal1`, `P_liza_anterior`, `Grupo_econ_mico`, `Ramo_12` y `Modal_3` | `Name`, campos de certificado y póliza | `Aseguradora1` y `Ramo` son picklists; `Ramo_12` es subform | Fechas de inicio/fin de póliza y certificado | Módulo central confirmado; tomador sin destino confirmado. |
| `Riesgos` | `Contratista`, `Contratante`, `Inmueble` | Sin lookup de póliza visible | `Tipo_de_riesgo` | `Fecha_inicio`, `Fecha_fin`, renovación SOAT | Relación con póliza no demostrada. |
| `Riesgos1` | `P_liza`, `Asegurado`, `Riesgo`, `Beneficiario`, `Afianzado_garantizado` | Lookup `P_liza` | `Aseguradora` es texto; `Ramo` es picklist | Fechas de ingreso/retiro y congelación | Estructura fuerte para asegurados; destinos de lookups pendientes. |
| `Opeeraciones` | `P_liza`, `P_liza_ARL`, `Tomador` | Múltiples campos de póliza/certificado | `Aseguradora` y `Ramo` son texto | Fechas de vigencia de póliza/certificado | Lookup de póliza existe; destino no informado. |
| `Directorio_Aseguradoras` | `Tipo_d_Contacto` y `C_digos` son subforms | Ninguno | Maestro/directorio de aseguradora | Ninguna | `Polizas.Aseguradora1` es picklist, no lookup a este módulo. |
| `Ramo_12` | `Parent_Id` lookup | Hijo estructural candidato de póliza | `Cobertura` picklist, no maestro de ramo | Ninguna | Coincidencia exacta con `Polizas.Ramo_12` permite asociación fuerte. |
| `Relaciones` | `Parent_Id`, `Nombre_del_cliente` | Ninguno | Ninguno | Ninguna | Coincide con `Contacts.Relaciones`; destino de persona pendiente. |
| `Informaci_n_familiar_y_co` | `Parent_Id`, `Personas` | Ninguno | Ninguno | Ninguna | Relación familiar candidata; destinos pendientes. |
| `Empleados` | subforms `Relacionados`, `Relacionados_Hijoss` | Ninguno | Ninguno | Ninguna | Relaciones internas de empleados, fuera del buscador de clientes. |

## 6. Mapeo propuesto: empresa

| Elemento | Propuesta | Nivel |
|---|---|---|
| Módulo | `Contacts`, filtrado por `Tipo_de_persona=Persona jurídica` | Candidato fuerte |
| Alternativa | `Persona_juridica` | Candidato fuerte por label, pero bloqueado y no determinado funcionalmente |
| ID técnico | `id` | Confirmado por metadata |
| Tipo de identificación | `Tipo_ID`, esperando `NIT` | Confirmado por metadata; uso real pendiente |
| NIT | `N_mero_de_ID` | Candidato fuerte |
| Razón social | `Raz_n_social` | Candidato fuerte |
| Nombre alterno | `Nombre_comercial` | Confirmado por metadata |
| Estado | `Estado` | Confirmado por metadata |
| Correo | `Email` | Confirmado por metadata |
| Teléfono | `Phone` y `Mobile` | Confirmado por metadata |
| Ciudad | `Ciudad_de_direcci_n_principal` | Candidato fuerte |
| Dirección | `Direcci_n` | Candidato fuerte |
| Pólizas | `Polizas.Tomador_principal1` | Campo lookup confirmado; destino no determinado |
| Riesgos | `Riesgos.Contratante`/`Contratista` | Campos lookup confirmados; destino no determinado |
| Asegurados | `Riesgos1.Asegurado` o `Tomador` textual | No determinado |

No debe implementarse un fallback hacia `Persona_juridica` ni `Accounts`. Si
`Contacts` no contiene las empresas esperadas, el flujo debe permanecer cerrado
hasta resolver los permisos de Fields API del módulo correcto.

## 7. Mapeo propuesto: individuo

| Elemento | Propuesta | Nivel |
|---|---|---|
| Módulo | `Contacts`, filtrado por `Tipo_de_persona=Persona natural` | Candidato fuerte |
| ID técnico | `id` | Confirmado por metadata |
| Tipo de documento | `Tipo_ID` | Confirmado por metadata |
| Número de documento | `N_mero_de_ID` | Candidato fuerte |
| Nombres | `First_Name` | Confirmado por metadata |
| Apellidos | `Last_Name` | Confirmado por metadata; además es `system_mandatory` |
| Nombre completo | `Full_Name` | Confirmado por metadata |
| Correo | `Email` | Confirmado por metadata |
| Teléfono | `Phone` y `Mobile` | Confirmado por metadata |
| Estado | `Estado` | Confirmado por metadata |
| Empresa relacionada | `Empresa` | Campo lookup confirmado; destino no determinado |
| Pólizas | No existe lookup inequívoco desde `Contacts` | No determinado |
| Asegurados | `Riesgos1.Asegurado` | Campo lookup confirmado; destino no determinado |
| Riesgos | `Riesgos1.Riesgo` | Campo lookup confirmado; destino no determinado |

`Empleados` también contiene `N_mero_ID`, pero su label, nombre de campo
principal y contactos de emergencia demuestran que representa empleados A&S,
no individuos clientes.

## 8. Relaciones demostrables

`relationships.json` no demuestra ningún destino de lookup. Solo pueden
afirmarse con seguridad estas asociaciones estructurales por referencia exacta
de subformulario y módulo hijo:

```text
Contacts
  -> Relaciones (subformulario del mismo API name)

Polizas
  -> Ramo_12 / Coberturas (subformulario del mismo API name)

Directorio_Aseguradoras
  -> Tipo_d_Contacto (subformulario del mismo API name)

Empleados
  -> Relacionados (subformulario del mismo API name)
  -> Relacionados_Hijoss (subformulario del mismo API name)
```

Relaciones de negocio pendientes:

```text
Empresa/Contacts
  -> Polizas: pendiente de validación funcional
  -> Riesgos: pendiente de validación funcional
  -> Asegurados: pendiente de validación funcional

Individuo/Contacts
  -> Polizas: pendiente de validación funcional
  -> Riesgos1/Asegurados: pendiente de validación funcional
  -> Riesgos: pendiente de validación funcional

Polizas
  -> Tomador (`Tomador_principal1`): destino pendiente
  -> Aseguradora: picklist, no relación a `Directorio_Aseguradoras`
  -> Ramo: picklist, no relación a un módulo maestro

Riesgos1/Asegurados
  -> Polizas (`P_liza`): destino pendiente
  -> Contact/asegurado (`Asegurado`): destino pendiente
  -> Riesgos (`Riesgo`): destino pendiente
```

## 9. Campos candidatos para los buscadores

### Empresa

- Búsqueda exacta por NIT: `Contacts.N_mero_de_ID`, restringida por
  `Tipo_ID=NIT` y `Tipo_de_persona=Persona jurídica`.
- Búsqueda parcial por razón social: `Contacts.Raz_n_social`.
- Alternativa de nombre: `Contacts.Nombre_comercial`.
- Resultado de selección: `id`, `Raz_n_social`, `Nombre_comercial`,
  `N_mero_de_ID` enmascarado, `Estado`, `Ciudad_de_direcci_n_principal`.
- Detalle: los anteriores más `Email`, `Phone`, `Mobile`, `Direcci_n`.

### Individuo

- Búsqueda exacta por documento: `Contacts.N_mero_de_ID`, restringida por
  `Tipo_de_persona=Persona natural` y el `Tipo_ID` permitido.
- Búsqueda parcial: `Full_Name`; como alternativa controlada, `First_Name` y
  `Last_Name`.
- Resultado de selección: `id`, `Full_Name`, `Tipo_ID`, documento enmascarado,
  `Estado` y `Empresa` si el lookup está poblado.
- Detalle: los anteriores más `First_Name`, `Last_Name`, `Email`, `Phone`,
  `Mobile`, `Ciudad_de_direcci_n_principal` y `Direcci_n`.

No puede afirmarse que `N_mero_de_ID` sea único. El indicador `unique` del
artefacto no es utilizable porque aparece verdadero para todos los campos.

## 10. Ambigüedades y riesgos

1. No se conoce si las empresas reales están en `Contacts`,
   `Persona_juridica`, `Accounts` o distribuidas entre ellos.
2. `Persona_juridica` y `Accounts` no entregaron Fields API.
3. No se conoce el formato real de `N_mero_de_ID` ni si conserva guiones o
   dígito de verificación.
4. No se conoce la cobertura de población de `Tipo_de_persona` y `Tipo_ID`.
5. Los destinos de todos los lookups de negocio están ausentes.
6. `Polizas.Aseguradora1` y `Polizas.Ramo` son picklists, no relaciones a
   módulos maestros.
7. `Asegurados_Colectivas`, `Asegurados_Greenland` y
   `Renovaciones_Vencimientos` son enlaces web, no módulos consultables.
8. `Coberturas_Asegurado` y `C_digos_Ramos` no aparecen en Modules API.
9. `Directorio_Aseguradoras` contiene campos con nombres de credenciales como
   `Clave_SARLAFT` y `Clave_portal`; sus valores quedan expresamente excluidos
   de cualquier probe o futura interfaz.
10. El probe actual resume los lookups como número de claves y no revela su
    módulo destino. Es adecuado para comprobar presencia, no para resolver el
    grafo relacional.

## 11. Comandos manuales mínimos

Estos comandos usan exclusivamente API names presentes en `fields.json`. El
probe enmascara todos los valores y no guarda resultados.

### Confirmar uso mixto de Contacts

```powershell
python manage.py colectivos_probe_data `
  --profile sandbox `
  --module Contacts `
  --fields id Tipo_de_persona Tipo_ID N_mero_de_ID First_Name Last_Name Full_Name Raz_n_social Nombre_comercial Estado Empresa `
  --limit 3 `
  --allow-real-read
```

### Confirmar presencia de tomador y clasificación de póliza

```powershell
python manage.py colectivos_probe_data `
  --profile sandbox `
  --module Polizas `
  --fields id Name Tomador_principal1 Estado_de_la_p_liza Ramo Aseguradora1 P_liza_Fecha_de_inicio_vigencia P_liza_Fecha_fin_de_la_vigencia `
  --limit 3 `
  --allow-real-read
```

### Confirmar estructura de asegurados

```powershell
python manage.py colectivos_probe_data `
  --profile sandbox `
  --module Riesgos1 `
  --fields id Name P_liza Asegurado Riesgo Estado Ramo `
  --limit 3 `
  --allow-real-read
```

### Confirmar estructura de riesgos

```powershell
python manage.py colectivos_probe_data `
  --profile sandbox `
  --module Riesgos `
  --fields id Name Contratista Contratante Tipo_de_riesgo Fecha_inicio Fecha_fin `
  --limit 3 `
  --allow-real-read
```

No se propone probe de `Persona_juridica`, `Accounts` o `Vendedores` porque sus
campos no pudieron validarse. Tampoco se consultan campos de credenciales del
directorio de aseguradoras.

## 12. Condición para empezar los buscadores

Antes de implementar vistas o servicios debe confirmarse:

1. que `Contacts` contiene registros de persona natural y jurídica;
2. que `Tipo_de_persona` está poblado de forma consistente;
3. que `Tipo_ID=NIT` y `N_mero_de_ID` representan el NIT de empresa;
4. que los documentos personales usan también `N_mero_de_ID`;
5. reglas de normalización y duplicados para `N_mero_de_ID`;
6. que `Raz_n_social` y `Full_Name` están suficientemente poblados;
7. destinos reales de `Tomador_principal1`, `P_liza`, `Asegurado`, `Riesgo` y
   `Empresa`;
8. autorización explícita para continuar usando `Contacts` si
   `Persona_juridica` permanece inaccesible.

Hasta completar esos puntos, la decisión correcta es no crear vistas, URLs,
formularios ni buscadores.

