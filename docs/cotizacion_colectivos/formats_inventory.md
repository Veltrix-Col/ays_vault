# Inventario de formatos de Colectivos

## Alcance comprobado

Se inspeccionaron los cuatro archivos físicamente presentes en
`cotizacion_colectivos/invitation_templates/source/`. Los paquetes `Salud.zip`,
`VG - Salud - Vehiculos - Asesoría y ventas.zip`,
`Vehiculos_otras aseguradoras.zip` y los archivos `40_*` citados en reuniones
no están en el worktree ni en los adjuntos accesibles; no se declaran
analizados.

| Ramo | Aseguradora | Archivo | Formato | Finalidad / categoría | Flujo | Estado | Precarga | Manual | Capacidad | Limitación |
|---|---|---|---|---|---|---|---|---|---:|---|
| Movilidad (40) | SURA | `movilidad/sura/Plantilla cotizacion Autos_Sura.xlsx` | XLSX | A. maestra de cotización | A&S → SURA | Activa | placa, modelo, marca, uso, ciudad, asegurado | Fasecolda, plan y campos no confirmados | 21 vehículos | Hojas auxiliares no se modifican |
| Movilidad (40) | Allianz | `movilidad/allianz/Plantilla Solicitud Cotizaciones_Autos colectivo.xlsx` | XLSX | A. maestra de solicitud | A&S → Allianz | Activa | tomador, documento fuente, vigencia, aseguradora actual, pago compatible y vehículos | Fasecolda y datos sin fuente | 299 vehículos | Conserva fórmulas, validaciones y protección |
| Vida Grupo (83) | SURA | `vida/sura/Plantilla Carga Masiva Sura_VG.xls` | XLS BIFF8 | H. carga masiva operativa | A&S → SURA | Catalogada e inactiva | potencial: póliza, afiliado, asegurado, parentesco y contacto | operación, fecha, género, coberturas, extraprimas, banco y suscripción | 40 filas | Sin escritor BIFF8 instalado con preservación demostrada; no se convierte |
| Vida Grupo (83) | Allianz | `vida/allianz/Formato Vida Grupo Colectiva_Allianz_EDM.xlsx` | XLSX | A. maestra de solicitud | A&S → Allianz | Activa | tomador, documento, ciudad, inicio/fin y compañía actual | procedimiento, clase, grupo, edades, intermediario, valores, amparos, condiciones y siniestralidad | 1 solicitud | La copia limpia valores de captura históricos |

## Evidencia estructural

### SURA Vida Grupo BIFF8

Dos hojas visibles; hoja principal 53 × 256 y beneficiarios 10 × 13; 177
estilos, con 4 y 7 rangos combinados. El análisis fue de lectura con `xlrd`;
no demostró capacidad de escritura preservando formato.

### Allianz Vida Grupo

Una hoja `FORMATO`, rango A1:P93, protegida; 165 celdas pobladas, 4 rangos
combinados, 14 fórmulas, 16 reglas de validación, sin comentarios ni
hipervínculos. Contiene listas y reglas numéricas. La generación OOXML modifica
solo celdas cerradas por catálogo, conserva las demás partes y verifica que el
SHA-256 de la maestra no cambie.

## Integridad de maestras (SHA-256 al 2026-08-13)

- Movilidad Allianz: `A9427DD0DC231D5C3CC8D0167F7A455D01E244751729172A94C445B0F81D28AB`
- Movilidad SURA: `6523C0F54AEE197D358878068218B47344C0656D8E4FEEDA5CEFAB19F3446FF8`
- Vida Allianz: `B6FBB0EEAA11D0D00F841F2C3C55D01C55167A11F1B33E60861AC89BDCD34B75`
- Vida SURA: `4E525EAE5ABFBD66E4A2AFBA6F3E7F74048FD50B773C7B1087474DFE63EADA89`

## Matriz de cobertura confirmada

| Ramo / aseguradora | SURA | Allianz | AXA | Bolívar | SBS / HDI |
|---|---|---|---|---|---|
| Movilidad | Maestra activa | Maestra activa | Referencia; archivo no disponible | Referencia; archivo no disponible | Mencionadas; sin archivo comprobable |
| Vida Grupo | BIFF8 disponible e inactivo | Maestra activa | Sin evidencia física | Sin evidencia física | Sin evidencia física |
| Salud | Material mencionado, no disponible | Pendiente de confirmar | Sin evidencia | Sin evidencia | Sin evidencia |

“Sin evidencia” no significa que el formato no exista; solo que no puede
afirmarse con el material local disponible.
