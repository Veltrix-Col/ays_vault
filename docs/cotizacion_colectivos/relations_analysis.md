# Análisis agregado de relaciones de Cotización – Colectivos

## 1. Alcance y seguridad

Perfil exclusivo `sandbox`, operaciones de solo lectura, campos cerrados y comparación de identificadores mediante HMAC efímeros. No se conservaron IDs, nombres, documentos, pólizas, hashes ni respuestas originales.

## 2. Decisiones de relación

| Relación técnica | Clasificación |
|---|---|
| `Polizas.Tomador_principal1` → `Contacts` | Parcialmente confirmada |
| `Riesgos1.Asegurado` → `Contacts` | Confirmada |
| `Riesgos1.P_liza` → `Polizas` | Confirmada |
| `Riesgos1.Riesgo` → `Riesgos` | Confirmada |
| `Riesgos.Contratante` → `Contacts` | Parcialmente confirmada |
| `Riesgos.Contratista` → `Contacts` | Parcialmente confirmada |

## 3. Pólizas

- Módulo: `Polizas`.
- Procesados: **101**.
- Resultado: **Completo**.

### Cobertura

| Campo | Poblado | Vacío |
|---|---:|---:|
| `Aseguradora1` | 101 | 0 |
| `Estado_de_la_p_liza` | 101 | 0 |
| `Grupo_econ_mico` | 0 | 101 |
| `Name` | 101 | 0 |
| `P_liza_Fecha_de_inicio_vigencia` | 101 | 0 |
| `P_liza_Fecha_fin_de_la_vigencia` | 101 | 0 |
| `P_liza_anterior` | 24 | 77 |
| `Ramo` | 101 | 0 |
| `Tomador_principal1` | 1 | 100 |

### `Tomador_principal1` → `Contacts`

- Clasificación: **Parcialmente confirmada**.
- Lookup con ID: 1.
- Coincidencias internas: 1.
- Sin coincidencia: 0.

| Estructura | Cantidad |
|---|---:|
| dict_id_and_name | 1 |
| empty | 100 |

### Distribución `Estado_de_la_p_liza`

| Valor | Cantidad |
|---|---:|
| Cancelada | 1 |
| Contrato finalizado | 1 |
| Finalizada | 32 |
| Reemplazada | 2 |
| Vencida | 29 |
| Vigente | 36 |


## 4. Asegurados / Riesgos1

- Módulo: `Riesgos1`.
- Procesados: **101**.
- Resultado: **Completo**.

### Cobertura

| Campo | Poblado | Vacío |
|---|---:|---:|
| `Asegurado` | 69 | 32 |
| `Aseguradora` | 101 | 0 |
| `Estado` | 101 | 0 |
| `Fecha_ingreso_riesgo` | 2 | 99 |
| `Fecha_salida_riesgo` | 0 | 101 |
| `Name` | 101 | 0 |
| `P_liza` | 15 | 86 |
| `Ramo` | 101 | 0 |
| `Riesgo` | 4 | 97 |

### `Asegurado` → `Contacts`

- Clasificación: **Confirmada**.
- Lookup con ID: 69.
- Coincidencias internas: 69.
- Sin coincidencia: 0.

| Estructura | Cantidad |
|---|---:|
| dict_id_and_name | 69 |
| empty | 32 |
### `P_liza` → `Polizas`

- Clasificación: **Confirmada**.
- Lookup con ID: 15.
- Coincidencias internas: 15.
- Sin coincidencia: 0.

| Estructura | Cantidad |
|---|---:|
| dict_id_and_name | 15 |
| empty | 86 |
### `Riesgo` → `Riesgos`

- Clasificación: **Confirmada**.
- Lookup con ID: 4.
- Coincidencias internas: 4.
- Sin coincidencia: 0.

| Estructura | Cantidad |
|---|---:|
| dict_id_and_name | 4 |
| empty | 97 |

### Distribución `Estado`

| Valor | Cantidad |
|---|---:|
| Activo | 98 |
| Cancelado | 3 |


## 5. Riesgos

- Módulo: `Riesgos`.
- Procesados: **112**.
- Resultado: **Completo**.

### Cobertura

| Campo | Poblado | Vacío |
|---|---:|---:|
| `Contratante` | 2 | 110 |
| `Contratista` | 2 | 110 |
| `Fecha_fin` | 1 | 111 |
| `Fecha_inicio` | 2 | 110 |
| `Inmueble` | 0 | 112 |
| `Name` | 112 | 0 |
| `Tipo_de_riesgo` | 12 | 100 |

### `Contratante` → `Contacts`

- Clasificación: **Parcialmente confirmada**.
- Lookup con ID: 2.
- Coincidencias internas: 2.
- Sin coincidencia: 0.

| Estructura | Cantidad |
|---|---:|
| dict_id_and_name | 2 |
| empty | 110 |
### `Contratista` → `Contacts`

- Clasificación: **Parcialmente confirmada**.
- Lookup con ID: 2.
- Coincidencias internas: 2.
- Sin coincidencia: 0.

| Estructura | Cantidad |
|---|---:|
| dict_id_and_name | 2 |
| empty | 110 |

### Distribución `Tipo_de_riesgo`

| Valor | Cantidad |
|---|---:|
| Contratos y/o proyectos | 2 |
| Inmuebles | 9 |
| Vehículos | 1 |
| empty | 100 |


## 6. Relaciones de negocio requeridas

La clasificación funcional final debe derivarse de las coincidencias anteriores:

1. Contact empresa → Pólizas: usar `Polizas.Tomador_principal1` solo si queda confirmada.
2. Contact individuo → Pólizas: misma relación técnica, condicionada al tipo del Contact.
3. Pólizas → Riesgos1/Asegurados: usar `Riesgos1.P_liza` solo si queda confirmada.
4. Contacts → Riesgos1/Asegurados: usar `Riesgos1.Asegurado` solo si queda confirmada.
5. Riesgos1/Asegurados → Riesgos: usar `Riesgos1.Riesgo` solo si queda confirmada.
6. Contacts → Riesgos: evaluar `Riesgos.Contratista` y `Riesgos.Contratante` por separado.
7. Empresa → individuos relacionados: no confirmada por el lookup `Contacts.Empresa` en el perfil previo; pendiente de validación funcional.
8. Individuo → empresa: no confirmada por el lookup `Contacts.Empresa` en el perfil previo; pendiente de validación funcional.

### Clasificación funcional final

| Relación | Clasificación | Evidencia |
|---|---|---|
| Contact empresa → Pólizas | Parcialmente confirmada | `Tomador_principal1` solo estuvo poblado en 1 de 101 pólizas; el ID coincidió, pero la muestra relacional es insuficiente. |
| Contact individuo → Pólizas | Parcialmente confirmada | Comparte la misma limitación y el perfilador no atribuye el único Contact a un segmento personal. |
| Pólizas → Riesgos1/Asegurados | Confirmada | 15 de 15 lookups con ID coincidieron con `Polizas`; cero inconsistencias. |
| Contacts → Riesgos1/Asegurados | Confirmada | 69 de 69 lookups con ID coincidieron con `Contacts`; cero inconsistencias. |
| Riesgos1/Asegurados → Riesgos | Confirmada | 4 de 4 lookups con ID coincidieron con `Riesgos`; cero inconsistencias. |
| Contacts → Riesgos | Parcialmente confirmada | `Contratista` y `Contratante` tuvieron 2 coincidencias cada uno, sin inconsistencias, pero por debajo del umbral mínimo. |
| Empresa → individuos relacionados | No confirmada | `Contacts.Empresa` estuvo vacío en los 101 Contacts perfilados. |
| Individuo → empresa | No confirmada | `Contacts.Empresa` estuvo vacío en los 101 Contacts perfilados. |

## 7. Limitaciones

- Las coincidencias prueban identidad técnica de IDs, no semántica comercial adicional.
- No se consultaron registros fuera de los módulos cerrados de cada perfilador.
- No se siguieron relaciones ni se escribió en Zoho.
- Una relación parcial o no confirmada no debe exponerse como funcional.

## 8. Recomendación de implementación

Implementar únicamente relaciones clasificadas como **Confirmada**. Las demás deben mostrarse como “Pendiente de validación” o permanecer ausentes.
