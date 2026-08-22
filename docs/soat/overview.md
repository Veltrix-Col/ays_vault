# Visión general de SOAT

## Problema de negocio

El módulo consolida por placa información de pólizas SOAT y Movilidad, determina la fila representativa de cada universo y genera un formato para analistas con trazabilidad de la selección.

## Alcance implementado

- Carga web de un único `.xlsx` con estructura compatible.
- Validación de tamaño, dimensiones, ZIP, macros/binarios y encabezados.
- Selección independiente SOAT/Movilidad por placa.
- Nueve criterios de “Gestión SOAT A&S”.
- Elección de `ID CARGA`, columnas derivadas y trazabilidad.
- Salida de dos libros: informe principal y soporte, conservando formato, filtros y fechas.
- Neutralización de fórmulas y eliminación de hipervínculos.
- Temporales aislados; resultado devuelto en memoria.

## Estado

| Área | Estado |
|---|---|
| Flujo web | Implementado |
| Modelos/migraciones SOAT | No existen; no son necesarios para el flujo actual |
| Pruebas automáticas | Implementadas en `soat/tests.py` |
| Conexión Zoho | No existe en este módulo; enlace opcional solamente |
| Ejecución standalone | Implementada dentro de `legacy_processor.py` |
| QA con archivos productivos | No confirmado por esta documentación |

## Fuera de alcance

No actualiza Zoho, no envía correo, no conserva historial, no autentica usuarios por sí mismo y no reemplaza una decisión de suscripción o cartera.
