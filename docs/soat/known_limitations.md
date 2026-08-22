# Limitaciones conocidas de SOAT

- URL pública respecto de CardManager; la restricción depende de `TOOLS_ACCESS_MODE`/infraestructura.
- No hay auditoría, historial, usuario ejecutor ni persistencia del resultado.
- Procesamiento sincrónico dentro del worker; archivos grandes pueden consumir memoria/timeout.
- Toda fila no-SOAT se clasifica como Movilidad, incluso si aparece otro ramo.
- Reglas contienen nombres propios y prioridades codificadas.
- Cambios de labels/estructura del reporte fuente requieren ajuste de aliases.
- El resumen Base64 no es cifrado ni autenticado.
- No existe dataset dorado ni comparación automática con el proceso externo anterior.
- La referencia opcional solo aplica al modo standalone.
- Los scripts históricos nombrados no están presentes para comparación.
- No se confirmó volumen máximo real, política de retención ni operación productiva.
