# Referencia operativa SOAT (en desuso)

El módulo web SOAT (`/soat/`) ya no requiere un archivo de referencia: el campo `Motivo cancelación`
se toma tal como viene en el Excel que sube el usuario, sin enriquecimiento externo. Esta carpeta y
`SOAT_prueba_3_Def.xlsx` quedan sin uso por el flujo web; consérvela solo si algún script standalone
(`soat/services/legacy_processor.py --referencia`) todavía la necesita.

Si se conserva algún archivo `.xlsx` aquí, sigue excluido de Git y no debe copiarse a `static/`,
`media/` ni una ruta pública.
