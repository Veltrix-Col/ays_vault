# Radiografía de pólizas representativas de Colectivos

## Alcance

Cinco pólizas autorizadas, perfil `production`, consultas cerradas de solo lectura mediante `integrations.zoho.get_zoho`. Los artefactos conservan únicamente metadata, cobertura, conteos y categorías; no contienen IDs, nombres, documentos, teléfonos, correos ni respuestas crudas.

| Póliza autorizada | Ramo esperado | Resultado | Riesgos1 | Riesgos |
|---|---|---|---:|---:|
| `091000811814` | Salud colectivo | profiled | 7 | 0 |
| `158140` | Exequial colectivo | profiled | 146 | 0 |
| `1000166` | Hogar colectivo | profiled | 8 | 8 |
| `083002914855` | Vida grupo deudores | profiled | 200 | 0 |
| `900000288971` | Movilidad colectivo | profiled | 137 | 135 |

## Fuentes locales

- `Novedades Junio_Fonconstruimos.xlsx`: cuatro secciones (nuevos descuentos, modificaciones, retiros y devoluciones). Columnas base: asociado, asegurado, póliza, ramo, aseguradora, pago/descuento y observaciones.
- `MT-CA-01 Matriz de Ramos (1).xlsx`: códigos 91 Salud colectivo, 86 Exequial colectivo, 28 Hogar colectivo, 83 Vida grupo deudores y 40 Movilidad colectivo. Se usó solo como referencia funcional, nunca para localizar registros Zoho.
