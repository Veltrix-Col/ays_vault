"""Conciliador de Facturación: módulo público y efímero de conciliación de
relaciones de asegurados (Movilidad, Salud, VG Voluntario, VG Deudores).

La lógica de negocio vive en el paquete `conciliador` (motor puro, sin Django).
Esta app solo aporta la capa web: formulario de carga, procesamiento efímero en
un directorio temporal y entrega del reporte + resumen, siguiendo el mismo patrón
que la app `soat`.
"""
