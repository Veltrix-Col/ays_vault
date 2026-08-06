# SOAT — documentación vigente

**Versión documental:** 1.0

**Actualización:** 2026-08-05

**Responsable técnico:** equipo Veltrix

## Objetivo, alcance y estado

El módulo SOAT transforma un XLSX con estructura de reporte Zoho en un informe operativo de cinco hojas. El flujo web es público dentro del modo de acceso general configurado, no requiere CardManager/MFA y procesa archivos en un directorio temporal sin persistirlos. No consulta Zoho: el vínculo configurado es únicamente informativo.

Flujo: cargar `.xlsx` → validar ZIP/estructura/límites → leer hoja compatible → separar SOAT y Movilidad → seleccionar una fila por placa en cada universo → calcular gestión e ID de carga → validar 1:1 → descargar XLSX.

## Índice

- [Visión general](overview.md)
- [Arquitectura funcional](functional_architecture.md)
- [Arquitectura técnica](technical_architecture.md)
- [Fuentes de datos](data_sources.md)
- [Reglas de procesamiento](processing_rules.md)
- [Mapeo de campos](field_mapping.md)
- [Lógica de selección](selection_logic.md)
- [Salidas Excel](excel_outputs.md)
- [Trazabilidad](traceability.md)
- [Configuración](configuration.md)
- [Ejecución](execution.md)
- [Despliegue](deployment.md)
- [Operación](operations.md)
- [Pruebas](testing.md)
- [Limitaciones conocidas](known_limitations.md)
- [Mejoras pendientes](pending_improvements.md)
- [Matriz de trazabilidad](traceability_matrix.md)
- [Transferencia técnica](technical_handover.md)

## Advertencias

- El archivo cargado puede contener datos personales: no debe adjuntarse a tickets ni logs.
- El flujo web no descarga desde Zoho ni usa la referencia histórica de `private_assets/soat`.
- Los nombres `informe_soat_analistas.py` e `informe_renovaciones.py` aparecen como contexto histórico, pero esos scripts no están presentes como archivos independientes en este repositorio.
- La matriz de nueve criterios está codificada; su aprobación funcional vigente debe confirmarla A&S.

## Pendientes principales

Autenticación/segmentación de acceso si deja de ser herramienta pública controlada, política de retención, operación programada, dataset dorado de regresión, prueba de volumen real y aprobación de reglas/campos por A&S.
