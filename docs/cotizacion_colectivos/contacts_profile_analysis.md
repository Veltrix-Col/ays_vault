# Perfil agregado de Contacts

## 1. Alcance

Diagnóstico de calidad de datos limitado al módulo `Contacts`, con campos fijos y operaciones de solo lectura. El informe no contiene documentos, nombres, identificadores, hashes ni respuestas originales.

## 2. Perfil y entorno

- Perfil: `sandbox`.
- Entorno confirmado: `sandbox`.
- Generado: 2026-08-01T04:36:00+00:00.
- Páginas procesadas: 1.

## 3. Total procesado

- Registros: **101**.
- Resultado: **Completo**.
- Motivo de detención: `fin_de_paginacion`.

## 4. Distribución por Tipo_de_persona

| Segmento | Cantidad |
|---|---:|
| Persona jurídica | 6 |
| Persona natural | 95 |

## 5. Distribución por Tipo_ID

### Persona jurídica

| Tipo ID | Cantidad |
|---|---:|
| NIT | 6 |
### Persona natural

| Tipo ID | Cantidad |
|---|---:|
| CC | 95 |

## 6. Cobertura de campos

### Persona natural

| Campo | Total | Poblado | Vacío | Cobertura |
|---|---:|---:|---:|---:|
| `N_mero_de_ID` | 95 | 95 | 0 | 100.0% |
| `First_Name` | 95 | 95 | 0 | 100.0% |
| `Last_Name` | 95 | 95 | 0 | 100.0% |
| `Full_Name` | 95 | 95 | 0 | 100.0% |
| `Empresa` | 95 | 0 | 95 | 0.0% |
| `Estado` | 95 | 95 | 0 | 100.0% |

### Persona jurídica

| Campo | Total | Poblado | Vacío | Cobertura |
|---|---:|---:|---:|---:|
| `N_mero_de_ID` | 6 | 6 | 0 | 100.0% |
| `Raz_n_social` | 6 | 3 | 3 | 50.0% |
| `Nombre_comercial` | 6 | 6 | 0 | 100.0% |
| `Full_Name` | 6 | 6 | 0 | 100.0% |
| `Last_Name` | 6 | 6 | 0 | 100.0% |
| `Estado` | 6 | 6 | 0 | 100.0% |

## 7. Patrones de documento

### Persona jurídica

| Patrón | Cantidad |
|---|---:|
| digits_only | 6 |

Longitudes: `{'10': 5, '8': 1}`.
### Persona natural

| Patrón | Cantidad |
|---|---:|
| digits_only | 95 |

Longitudes: `{'10': 77, '3': 1, '7': 7, '8': 10}`.

### NIT

| Patrón | Cantidad |
|---|---:|
| digits_only | 6 |

- Longitud antes del guion: `{}`.
- Longitud después del guion: `{}`.

## 8. Duplicados agregados

### Comparación exacta tras strip exterior

| Segmento | Tipo ID | Documentos repetidos | Registros afectados |
|---|---|---:|---:|
| Ninguno | Ninguno | 0 | 0 |

### Comparación analítica sin espacios, puntos ni guiones

| Segmento | Tipo ID | Documentos repetidos | Registros afectados |
|---|---|---:|---:|
| Ninguno | Ninguno | 0 | 0 |

Los HMAC fueron efímeros, permanecieron en memoria durante la ejecución y no se guardaron ni imprimieron.

## 9. Inconsistencias agregadas

| Regla | Cantidad |
|---|---:|
| Sin valores | 0 |

## 10. Estructura del lookup Empresa

| Estructura | Cantidad |
|---|---:|
| empty | 101 |

No se siguió el lookup y no se mostraron sus valores.

## 11. Recomendación para buscador de empresas

- `Contacts` como módulo: evidencia suficiente por presencia de personas jurídicas.
- Filtro obligatorio: `Tipo_de_persona = Persona jurídica` y `Tipo_ID = NIT`.
- Documento exacto: `N_mero_de_ID`, conservando una lista de selección cuando existan coincidencias múltiples.
- Nombre principal por cobertura observada: `Nombre_comercial`.
- Fallback: `Raz_n_social`.

## 12. Recomendación para buscador de individuos

- `Contacts` como módulo: evidencia suficiente por presencia de personas naturales.
- Filtro obligatorio: `Tipo_de_persona = Persona natural`; usar también `Tipo_ID` cuando el usuario conozca el tipo documental.
- Documento exacto: `N_mero_de_ID`.
- Nombre principal: `Full_Name`; para presentación estructurada pueden usarse `First_Name` y `Last_Name`.

## 13. Normalización documental recomendada

Aplicar inicialmente `strip` exterior y comparación exacta. La eliminación de espacios, puntos y guiones se utilizó solo para diagnosticar equivalencias y debe adoptarse en búsqueda únicamente después de validar su impacto por tipo documental. Para NIT, consultar el valor exacto registrado; la muestra no demuestra un dígito de verificación separado y no autoriza a quitar el último dígito, sin mezclar tipos de identificación.

## 14. Decisión técnica

1. Contacts para empresas: **sí**.
2. Contacts para individuos: **sí**.
3. `N_mero_de_ID` para búsqueda exacta: **sí, combinado con segmento y tipo documental**.
4. Exigir `Tipo_ID`: **sí, para reducir falsos positivos y separar NIT de documentos personales**.
5. Campo principal de empresa: **`Nombre_comercial`**.
6. Fallback de empresa: **`Raz_n_social`**.
7. Campo principal del individuo: **`Full_Name`**.
8. Normalización sin falsos positivos demostrados: **solo strip exterior**; normalización adicional queda condicionada por tipo.
9. NIT con dígito de verificación: **consultar el valor exacto registrado; la muestra no demuestra un dígito de verificación separado y no autoriza a quitar el último dígito**.
10. Duplicados y selección: **no se detectaron en los registros procesados, pero la interfaz debe tolerarlos**.
11. Inconsistencias: deben manejarse como resultados incompletos, nunca corregirse automáticamente ni ocultarse.
12. Evidencia para ambos buscadores: **suficiente para un diseño defensivo**.

## 15. Limitaciones

- El resultado describe únicamente el estado observado en Sandbox al momento de ejecución.
- No valida relaciones con pólizas, asegurados o riesgos.
- Las equivalencias normalizadas son diagnósticas y no autorizan una transformación destructiva.
- Un resultado parcial no representa la totalidad del módulo.

## 16. Pendientes de relaciones

Validar por metadata y un muestreo independiente, expresamente autorizado, los lookups entre `Contacts`, `Polizas`, `Riesgos1` y demás módulos. Este comando no consultó esos módulos ni siguió relaciones.
