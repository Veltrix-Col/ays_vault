# Matriz de trazabilidad de SOAT

| ID | Requisito | Estado | Componente | Prueba/evidencia | Observación / pendiente |
|---|---|---|---|---|---|
| ST-01 | Cargar XLSX compatible | Implementado/probado | formulario/vista | tests de nombre/estructura | QA real pendiente |
| ST-02 | Rechazar archivos peligrosos | Implementado | `SoatUploadForm` | ZIP/macro/traversal por código | ampliar tests específicos |
| ST-03 | Selección por placa | Implementado | `seleccionar_por_placa` | reglas en código | aprobación A&S pendiente |
| ST-04 | Independencia SOAT/Movilidad | Implementado | procesador | llamadas separadas | confirmar otros ramos |
| ST-05 | Nueve criterios de gestión | Implementado/probado | legacy processor | test de nueve criterios | nombres/prioridades por aprobar |
| ST-06 | ID CARGA | Implementado/validado | builder/validator | validación interna | QA con casos vacíos |
| ST-07 | Dos libros con hojas separadas | Implementado/probado | exportación | test de descarga | QA visual pendiente |
| ST-08 | Integridad 1:1 | Implementado | validadores | código de validación | dataset dorado pendiente |
| ST-09 | Fórmulas/links saneados | Implementado/probado parcialmente | processor | test de fórmula | test explícito de links pendiente |
| ST-10 | Temporales aislados | Implementado/probado | `TemporaryDirectory` | test concurrente | monitoreo host pendiente |
| ST-11 | Sin escritura/consulta Zoho | Implementado por arquitectura | vista/procesador | ausencia de cliente API | URL es solo enlace |
| ST-12 | Auditoría operativa | No implementada | — | — | definir si es requerida |
