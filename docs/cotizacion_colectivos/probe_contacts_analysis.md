# Análisis del probe real de Contacts en Zoho Sandbox

## 1. Objetivo

Validar con una muestra mínima si `Contacts` contiene personas naturales y
jurídicas, qué campos aparecen poblados y si la evidencia permite definir los
dos buscadores de Cotización – Colectivos.

Este documento no contiene valores personales, IDs, documentos, nombres ni
respuestas de Zoho. Solo conserva presencia, tipo y longitud agregada.

## 2. Comando ejecutado

```powershell
python manage.py colectivos_probe_data `
  --profile sandbox `
  --module Contacts `
  --fields id Tipo_de_persona Tipo_ID N_mero_de_ID First_Name Last_Name Full_Name Raz_n_social Nombre_comercial Estado Empresa `
  --limit 3 `
  --allow-real-read
```

Fue el único comando que realizó una consulta real a Zoho durante esta
intervención.

## 3. Controles confirmados

- Perfil solicitado: `sandbox`.
- Entorno reportado por Organization API: `sandbox`.
- Backend: SDK, modo de lectura.
- Módulo consultado: únicamente `Contacts`.
- Registros solicitados y recibidos: 3.
- No se consultó Production.
- No se modificaron ni almacenaron registros.
- El probe imprimió solo tipo, longitud, presencia o resumen de lookup.

## 4. Campos consultados

- `id`
- `Tipo_de_persona`
- `Tipo_ID`
- `N_mero_de_ID`
- `First_Name`
- `Last_Name`
- `Full_Name`
- `Raz_n_social`
- `Nombre_comercial`
- `Estado`
- `Empresa`

## 5. Resumen seguro de la muestra

| Característica | Registro 1 | Registro 2 | Registro 3 |
|---|---:|---:|---:|
| `Tipo_de_persona` longitud | 15 | 16 | 15 |
| `Tipo_ID` longitud | 2 | 3 | 2 |
| `N_mero_de_ID` longitud | 3 | 10 | 8 |
| `First_Name` | presente | vacío | presente |
| `Last_Name` | presente | presente | presente |
| `Full_Name` | presente | presente | presente |
| `Raz_n_social` | vacío | vacío | vacío |
| `Nombre_comercial` | vacío | presente | vacío |
| `Estado` longitud | 7 | 7 | 9 |
| `Empresa` | vacío | vacío | vacío |

Los IDs técnicos fueron numéricos de 19 dígitos, pero sus valores no se
conservaron ni mostraron.

## 6. Hallazgos sobre empresas

La metadata limita `Tipo_de_persona` a `Persona natural` y
`Persona jurídica`. Al cruzar esa lista cerrada con las longitudes observadas:

- longitud 15 corresponde a `Persona natural`;
- longitud 16 corresponde a `Persona jurídica`.

Por tanto, uno de los tres registros es compatible con una persona jurídica.
Esta identificación es una deducción segura de metadata más longitud; el probe
no imprimió el valor literal del registro.

En ese registro:

- `Tipo_ID` está presente y tiene longitud 3;
- `N_mero_de_ID` está presente y tiene longitud 10;
- `First_Name` está vacío;
- `Last_Name` está poblado;
- `Full_Name` está poblado;
- `Raz_n_social` está vacío;
- `Nombre_comercial` está poblado;
- `Estado` está poblado;
- `Empresa` está vacío.

Conclusiones:

- `Contacts` sí contiene al menos un registro compatible con persona jurídica.
- La empresa observada usa campos compartidos con personas: `Last_Name` y
  `Full_Name`.
- `Nombre_comercial` aporta el dato corporativo observable en esta muestra.
- `Raz_n_social` no puede definirse como campo principal porque estuvo vacío.
- Un `Tipo_ID` de longitud 3 es compatible con `NIT`, pero también existen
  otros valores de tres caracteres en el picklist. No se puede afirmar que el
  valor observado sea `NIT` sin romper la redacción actual.

## 7. Hallazgos sobre individuos

Dos registros tienen `Tipo_de_persona` de longitud 15, compatible con
`Persona natural` según el picklist cerrado.

En ambos:

- `Tipo_ID` está presente y tiene longitud 2;
- `N_mero_de_ID` está presente;
- `First_Name`, `Last_Name` y `Full_Name` están poblados;
- `Raz_n_social` y `Nombre_comercial` están vacíos;
- `Estado` está poblado;
- `Empresa` está vacío.

Conclusiones:

- `Contacts` sí contiene registros compatibles con personas naturales.
- `Full_Name` es el candidato principal para visualización.
- `First_Name` y `Last_Name` son componentes disponibles para búsquedas o
  presentación controlada.
- La longitud 2 de `Tipo_ID` no permite distinguir entre `CC`, `CE`, `RC`, `TI`,
  `PP` o `EX`; el tipo exacto no fue expuesto.

## 8. Formato observado de documentos

Solo se observaron longitudes de 3, 10 y 8 caracteres. No hubo documentos
nulos en la muestra.

El probe actual no permite determinar:

- si los caracteres son exclusivamente dígitos;
- presencia de puntos;
- guiones;
- espacios;
- dígito de verificación;
- prefijos;
- ceros iniciales;
- mayúsculas o minúsculas.

Los tres documentos tienen longitudes diferentes, por lo que no pueden ser
duplicados literales entre sí. Esto no permite evaluar duplicados en el resto
de la organización.

No debe implementarse todavía una normalización que elimine puntuación. La
única transformación segura demostrada es recortar espacios exteriores. Una
intervención posterior podría ampliar el probe para emitir únicamente banderas
como `solo_digitos`, `contiene_guion`, `contiene_puntos` y
`contiene_espacios`, sin mostrar el valor.

## 9. Estructura observada de Empresa

`Contacts.Empresa` estuvo vacío en los tres registros. Por consiguiente:

- no se observó un objeto lookup;
- no puede confirmarse si devuelve `id`, `name`, ambos u otra estructura;
- no puede determinarse su módulo destino;
- no debe utilizarse todavía para navegar desde individuo hacia empresa.

## 10. Calidad de datos observable

- Nulos: `Raz_n_social` estuvo vacío en los tres registros; `Empresa` también.
- Campos corporativos: `Nombre_comercial` estuvo presente solo en el registro
  compatible con persona jurídica.
- Campos personales: los dos registros compatibles con persona natural tienen
  nombres completos y componentes de nombre.
- Uso mixto: la persona jurídica usa `Last_Name` y `Full_Name`, además de
  `Nombre_comercial`.
- Inconsistencias evidentes de tipo: no se observan con la información
  redactada, pero no pueden descartarse.
- Duplicados: no hubo duplicados literales posibles dentro de la muestra por
  diferencia de longitudes; no es una conclusión estadística.

## 11. Limitaciones

- La muestra es de tres registros y no representa toda la organización.
- Solo se recibió el orden natural de la consulta; no se seleccionaron
  intencionalmente tres empresas y tres individuos.
- El probe no revela valores de picklist ni patrones de caracteres.
- No permite comprobar el valor exacto de `Tipo_ID`.
- No permite comprobar destinos de lookup cuando el campo está vacío.
- No permite evaluar unicidad global ni cobertura de población.
- No se consultaron pólizas, asegurados, riesgos ni otros módulos.

## 12. Decisión técnica

**Resultado: parcialmente suficiente.**

La muestra confirma que `Contacts` puede contener ambos tipos de entidad y que
el discriminador estructural es `Tipo_de_persona`. También confirma la presencia
de documento y los patrones de campos personales/corporativos.

No es suficiente para implementar los buscadores porque aún faltan:

1. confirmar el valor real `Tipo_ID=NIT` en personas jurídicas;
2. conocer el patrón seguro de `N_mero_de_ID`;
3. definir el campo principal de empresa cuando `Raz_n_social` está vacío;
4. conocer cobertura y duplicados;
5. observar la estructura de `Empresa`;
6. confirmar relaciones con pólizas y asegurados.

## 13. Mapeo propuesto después del probe

### Empresa

| Uso | Campo | Estado |
|---|---|---|
| Módulo | `Contacts` | Candidato fuerte respaldado por muestra |
| Discriminador | `Tipo_de_persona = "Persona jurídica"` | Confirmado por metadata y compatible con muestra |
| Tipo de documento | `Tipo_ID` | Campo confirmado; valor `NIT` pendiente en registros |
| Documento | `N_mero_de_ID` | Candidato fuerte; formato pendiente |
| Nombre principal | `Nombre_comercial` | Poblado en la empresa observada; cobertura pendiente |
| Alternativas | `Full_Name`, `Last_Name` | Poblados en la empresa observada |
| Razón social | `Raz_n_social` | Campo confirmado, pero vacío en la muestra |
| Estado | `Estado` | Poblado |

### Individuo

| Uso | Campo | Estado |
|---|---|---|
| Módulo | `Contacts` | Candidato fuerte respaldado por muestra |
| Discriminador | `Tipo_de_persona = "Persona natural"` | Confirmado por metadata y compatible con muestra |
| Tipo de documento | `Tipo_ID` | Campo confirmado; tipo exacto pendiente |
| Documento | `N_mero_de_ID` | Candidato fuerte; formato pendiente |
| Nombre principal | `Full_Name` | Poblado en ambos individuos observados |
| Nombres | `First_Name` | Poblado en ambos individuos observados |
| Apellidos | `Last_Name` | Poblado en ambos individuos observados |
| Estado | `Estado` | Poblado |

## 14. Próximo paso recomendado

No crear todavía vistas ni buscadores. La siguiente intervención debería ser
una de estas dos opciones, expresamente autorizada:

1. mejorar el probe para informar categorías seguras de picklist y patrón del
   documento sin mostrar valores; o
2. validar manualmente en la interfaz de Zoho Sandbox un conjunto controlado de
   personas naturales y jurídicas.

Después deben resolverse por metadata o validación segura los destinos de
`Polizas.Tomador_principal1`, `Riesgos1.P_liza`, `Riesgos1.Asegurado`,
`Riesgos1.Riesgo` y `Contacts.Empresa`.

