# Reportes y exportes

## Implementación

El centro de reportes genera XLSX y PDF mediante POST. Los reportes aplican filtros de fecha/actor/evento/IP y conjuntos autorizados. `ReportExport` conserva estado y resultado técnico, no el archivo exportado como repositorio documental.

## Seguridad

- Límites predeterminados: XLSX 5.000 filas, PDF 1.000; ventana por defecto 90 días.
- Exportes grandes exigen acotar filtros y generan señales según umbral.
- XLSX usa encabezados/formatos/filtros y neutraliza prefijos `=`, `+`, `-`, `@`.
- PDF usa WeasyPrint y branding estático.
- Respuestas son privadas/no-store y excluyen valores protegidos.
- Éxito y fallo se auditan con códigos saneados.

## Acceso

Las pruebas confirman restricción de reportes y CSRF/POST. La autorización exacta depende de la vista y del rol, no de ocultar enlaces.

## Validación pendiente

Se requiere prueba manual con volúmenes representativos, fuentes disponibles en Linux y consumo de memoria del contenedor.
