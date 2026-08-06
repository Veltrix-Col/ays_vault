# Transferencia técnica — SOAT

**Proyecto:** `ays_tc_vault`

**Módulo:** `soat`

**Versión documental:** 1.0

**Fecha:** 2026-08-05

## 1. Resumen ejecutivo

SOAT es un pipeline Django síncrono para convertir un reporte XLSX en un informe operativo de cinco hojas. No usa modelos, OAuth ni APIs Zoho; trabaja con el archivo cargado, temporales aislados y salida en memoria. Separa SOAT/Movilidad, elige una fila completa por placa en cada universo, deriva gestión e ID de carga, valida integridad y formatea el libro. La implementación tiene pruebas automáticas, pero reglas, volumen y operación real requieren validación A&S.

## 2. Objetivo, alcance y estado

Resuelve la consolidación por placa para analistas. Implementa carga, validación, selección, derivación, trazabilidad y exportación. No conserva historial, no actualiza Zoho, no autentica por CardManager y no programa ejecuciones. Su estado es funcional en código; no se afirma QA productivo.

## 3. Arquitectura funcional y técnica

```text
Operador → /soat/
 → formulario/validación ZIP y esquema
 → temporal aislado
 → pandas/openpyxl
 → selección SOAT + selección Movilidad
 → reglas/ID/trazabilidad
 → validación 1:1
 → XLSX bytes → descarga no-store
```

`processor.py` adapta el motor a web y sanea la salida. `legacy_processor.py` contiene reglas, tablas, validación, formato y un CLI opcional. El flujo web no usa la referencia histórica.

## 4. Componentes e inventario

| Archivo | Responsabilidad |
|---|---|
| `soat/forms.py` | extensión, tamaño, ZIP, macros y estructura |
| `soat/views.py` | página, POST, errores y descarga |
| `soat/services/processor.py` | temporales, orquestación, saneamiento y resumen |
| `soat/services/legacy_processor.py` | lectura, selección, reglas, validación, Excel y CLI |
| `soat/urls.py` | `/soat/` |
| `templates/soat/upload.html` | interfaz |
| `static/css/soat.css`, `static/js/soat.js` | presentación/interacción |
| `soat/tests.py` | regresión |

No hay modelos, admin, middleware o migraciones propios.

## 5. Modelo de datos y fuentes

El “modelo” es tabular y efímero: DataFrames de fuente, subconjuntos SOAT/Movilidad, seleccionados, formato y trazabilidad. La fuente mínima contiene Placa/Ramo; el motor reconoce campos de responsables, póliza, estado, entidad/persona, contacto e IDs. La clasificación actual considera SOAT solo el ramo exacto y Movilidad todo lo demás.

## 6. Flujo y reglas de negocio

Por placa/universo se prioriza póliza vigente, asegurado activo, fin de vigencia más reciente y última fila fuente. La fila ganadora no se mezcla. Gestión usa nueve reglas en el orden documentado en [processing_rules.md](processing_rules.md). `ID CARGA` prefiere ID SOAT y luego Movilidad. Se valida resultado 1:1, no duplicados y coherencia de derivaciones.

## 7. Seguridad

Solo `.xlsx`, límites de tamaño/dimensiones, ZIP sin traversal/absolutos/macros/binarios y límite de descompresión. Temporales por ejecución, fórmulas neutralizadas, hipervínculos eliminados, errores saneados y respuesta no-store/nosniff. Riesgo: el módulo es público respecto de CardManager y procesa datos sensibles en el worker; la frontera real debe ser intranet/proxy configurado.

## 8. Integraciones y configuración

Pandas/openpyxl y sistema temporal. `SOAT_ZOHO_REPORT_URL` es un enlace HTTP(S), no una integración. Variables: tamaño, filas, columnas, URLs y acceso global; consulte [configuration.md](configuration.md). No usa correo, DB, OAuth ni SDK.

## 9. Operación y despliegue

Se despliega dentro del mismo contenedor Django. Requiere memoria, timeout y temporal suficientes. Operación: conservar fuente, cargar, revisar conteos/trazabilidad, custodiar salida. Recovery consiste en repetir determinísticamente desde la fuente original. No hay scheduler ni almacenamiento.

## 10. Logs, observabilidad, auditoría

Errores inesperados se registran y el cliente recibe mensaje genérico; funcionales retornan 422. El encabezado de resumen expone solo agregados. No existe auditoría SOAT persistente ni eventos Vault. Si se requiere trazabilidad de quién ejecuta, debe diseñarse sin asumir que ya existe.

## 11. Pruebas y resultados

Siete pruebas declaradas cubren acceso, estructura, descarga, fórmula, concurrencia y nueve criterios. Faltan navegador, volumen máximo, cada ataque ZIP por separado, hipervínculos y dataset dorado. El resultado de ejecución de esta actualización se informa en la entrega final; QA A&S sigue pendiente.

## 12. Limitaciones, riesgos y pendientes

Acceso público, proceso síncrono, memoria, clasificación amplia Movilidad, reglas con nombres propios, esquema frágil a labels, sin historial/auditoría y sin proceso externo disponible para comparación. Priorizar aprobación funcional, dataset dorado, frontera de acceso y prueba de carga.

## 13. Soporte y recuperación

Solicitar dimensiones/encabezados/conteos y error, no datos personales. Verificar tipo XLSX, estructura, límites y columnas. Reproducir con copia saneada. Repetir desde fuente intacta; no hay estado de base que restaurar. Escalar cambios de reglas a A&S antes de modificar código.

## 14. Trazabilidad

Consulte [traceability_matrix.md](traceability_matrix.md). ST-01–ST-11 tienen evidencia de código; ST-12 (auditoría operativa) no está implementado.

## 15. Inventarios

- **Archivos principales:** tabla de sección 4, settings generales y dependencias.
- **Comandos:** web por `runserver`; CLI embebido en `legacy_processor.py`, sin management command SOAT.
- **Variables:** [configuration.md](configuration.md).
- **Hojas/campos:** [excel_outputs.md](excel_outputs.md) y [field_mapping.md](field_mapping.md).

## 16. Confirmaciones y conclusión

El pipeline está claramente aislado y conserva integridad por placa, pero no se declara completamente validado por A&S. Esta actualización no procesó archivos reales, no llamó Zoho, no envió correo y no cambió código, modelos, migraciones ni seguridad.

## Anexos

- [Lógica de selección](selection_logic.md)
- [Reglas](processing_rules.md)
- [Trazabilidad](traceability.md)
- [Operación](operations.md)

## Índice de conformidad del handover

| Contenido obligatorio | Ubicación |
|---|---|
| 1. Portada textual | Encabezado |
| 2–5. Resumen, objetivo, alcance y estado | Secciones 1–2 |
| 6–8. Arquitecturas y componentes | Secciones 3–4 |
| 9–11. Modelo tabular, flujo y reglas | Secciones 3, 5 y 6 |
| 12–13. Seguridad e integraciones | Secciones 7–8 |
| 14–16. Configuración, operación y despliegue | Secciones 8–9 |
| 17–18. Logs/observabilidad y auditoría | Sección 10; auditoría persistente declarada ausente |
| 19–20. Pruebas y resultados | Sección 11 y entrega final de esta intervención |
| 21–24. Limitaciones, riesgos, pendientes y recomendaciones | Sección 12 y `pending_improvements.md` |
| 25–26. Soporte y recuperación | Sección 13 |
| 27. Matriz de trazabilidad | Sección 14 y `traceability_matrix.md` |
| 28–30. Archivos, comandos y variables | Sección 15 |
| 31–32. Confirmaciones y conclusión | Sección 16 |
| 33. Anexos | Lista anterior |
