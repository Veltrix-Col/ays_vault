# Plantillas maestras de invitación

## 1. Alcance y estado

Este documento registra el análisis técnico y funcional de las cuatro maestras ubicadas, por ramo y aseguradora, en `cotizacion_colectivos/invitation_templates/source/`. El análisis se hizo en modo lectura; los binarios originales no se modifican. La generación usa únicamente el Policy Workspace/Snapshot cifrado vigente y no inicializa la fachada Zoho.

Estado de implementación:

- Movilidad colectivo (`40`): SURA y Allianz activas; se descargan juntas en ZIP.
- Vida grupo deudores (`83`): Allianz activa; SURA analizada pero desactivada porque el `.xls` BIFF8 no puede editarse con preservación demostrable usando las dependencias portables del proyecto.
- Otros ramos: sin maestra registrada; el preview lo informa sin inventar mappings.

## 2. Matriz consolidada de las maestras

| Plantilla | Aseguradora | Ramo | Propósito | Extensión | Hojas | Región de captura | Estado |
|---|---|---|---|---|---|---|---|
| Plantilla Carga Masiva Sura_VG.xls | SURA | Vida grupo deudores | Carga masiva de asegurados, coberturas, extraprimas y beneficiarios | XLS BIFF8 | Plantilla Cargas Masivas; Plantilla Solo Beneficiarios | Fila 14 a 53 en la hoja principal | Analizada; no generable de forma segura |
| Plantilla cotizacion Autos_Sura.xlsx | SURA | Movilidad colectivo | Cotización de automóviles | XLSX | Riesgos; Ayudas; Riesgos_Cotizador | Riesgos, filas 2 a 22 | Activa |
| Plantilla Solicitud Cotizaciones_Autos colectivo.xlsx | Allianz | Movilidad colectivo | Solicitud de cotización colectiva y relación de vehículos | XLSX | Formato Solicitud Cotización; Datos Vehículos a cotizar; Observaciones | B3:B17 y vehículos filas 2 a 300 | Activa |
| Formato Vida Grupo Colectiva_Allianz_EDM.xlsx | Allianz | Vida grupo deudores | Solicitud de cotización colectiva | XLSX | FORMATO | A1:P93; captura principal en columna B y siniestralidad A85:C87 | Activa con precarga conservadora |

Las maestras Allianz se clasifican con evidencia interna del libro, no solo por el nombre del archivo. Cuando existen propiedades personalizadas históricas, el generador las vacía **solo en la copia descargable**. El original permanece intacto y los demás componentes del paquete se conservan.

Rutas físicas confirmadas:

- `movilidad/sura/Plantilla cotizacion Autos_Sura.xlsx`;
- `movilidad/allianz/Plantilla Solicitud Cotizaciones_Autos colectivo.xlsx`;
- `vida/sura/Plantilla Carga Masiva Sura_VG.xls`;
- `vida/allianz/Formato Vida Grupo Colectiva_Allianz_EDM.xlsx`.

## 3. SURA Vida Grupo — XLS antiguo

### Estructura

- Formato: Compound File/BIFF8, Excel 97–2003, sin VBA.
- `Plantilla Cargas Masivas`: rango usado `A1:AV53`; 48 columnas; encabezados en fila 1; ejemplos e instrucciones en filas 2–12; separador combinado en fila 13; captura preparada en filas 14–53.
- Celdas combinadas: `L3:P3`, `W3:AC3`, `AJ3:AP3` y fila 13.
- `Plantilla Solo Beneficiarios`: rango `A1:M10`; combina las columnas de ejemplo `A:C` y `J:M` en filas 2–10; funciona como instrucción/ejemplo, no como una tabla de captura limpia confirmada.
- Hojas ocultas: ninguna.
- Fórmulas: ninguna observada.
- Hipervínculos: ninguno observado.
- Validaciones: no se confirmaron objetos de validación Excel.
- Estilos: las 48 columnas y la zona de captura conservan estilos; determinadas entradas están resaltadas. No se alteraron.

### Encabezados principales

Número de póliza, operación, fecha de movimiento, identificación del afiliado, identificación del asegurado, nombre, género, fecha de nacimiento, parentesco, cinco valores asegurados, gestor, subgrupo, número de riesgo, dependencia, nómina, crédito, siete extraprimas, identificación y nombre de beneficiario, parentesco, distribución, contingencia, cuenta bancaria, banco, identificación del cuentahabiente, contacto, diagnóstico, exclusiones, coberturas a eliminar, radicado y causa.

### Decisión de compatibilidad

La instalación actual declara `openpyxl`, que no escribe BIFF8. `xlrd`/`xlwt` no están declarados y tampoco garantizan conservar completamente estilos, fórmulas y estructura de una maestra existente. Microsoft Excel COM permitió inspección local de solo lectura, pero no es portable al despliegue Django/Linux ni constituye una biblioteca del proyecto. Por ello:

- no se convierte a XLSX;
- no se genera una copia degradada;
- el catálogo la muestra como no disponible;
- queda pendiente recibir una maestra XLSX oficial o aprobar un servicio Windows/Excel automatizado y verificable.

### Datos potencialmente precargables cuando exista soporte seguro

Póliza, operación, fecha efectiva, identificaciones y nombres de afiliado/asegurado/beneficiario, parentesco, plan/subgrupo, riesgo, valores económicos presentes y datos de contacto presentes en Snapshot. Permanecen manuales los datos bancarios, diagnóstico, radicado, decisiones de extraprima/exclusión y cualquier campo de suscripción no confirmado.

## 4. SURA Autos

### Estructura

- `Riesgos`: `A1:T22`; encabezados en fila 1; 21 filas de captura; sin celdas combinadas, fórmulas, validaciones, hipervínculos ni protección.
- `Ayudas`: `A1:AK17`; listas e instrucciones; no se modifica.
- `Riesgos_Cotizador`: `A1:CL1`; 90 encabezados sin filas de captura confirmadas; no se modifica.
- Hojas ocultas: ninguna.
- Fórmulas, hipervínculos, validaciones y rangos con nombre: no observados.
- Los estilos de las filas vacías se conservan porque el generador reemplaza solo el contenido XML de celdas existentes.

Campos marcados como obligatorios por la maestra: placa, modelo, Fasecolda, servicio, ciudad, plan, tipo y número de identificación del asegurado, y nombre o razón social según corresponda.

## 5. Allianz Autos colectivo

### Estructura

- `Formato Solicitud Cotización`: rango `A2:E40`; formulario en `B3:B17`; `A2:B2` combinado; fórmula `B3=TODAY()`; tres validaciones de lista en `B7`, `B14` y `B16`; columna D y filas auxiliares ocultas; tres comentarios; sin protección.
- `Datos Vehículos a cotizar`: rango `A1:AZ300`; tabla funcional `A:L`; filas 2–300 desbloqueadas; hoja protegida; autofiltro `A1:L1`; validaciones en uso (`K`) y relación con tomador (`L`); listas auxiliares en `AX` y `AZ`.
- `Observaciones`: `A1:B20`; protegida; referencia de usos y relaciones.
- Hojas ocultas: ninguna.
- Hipervínculos: ninguno.
- Rango con nombre funcional confirmado: `TRANSPORTE_DE_MERCANCIAS_PROPIAS`; la maestra conserva además rangos internos.
- No se sobrescribe la fecha `B3`: la fórmula se conserva.

La hoja admite personas o empresas en la relación de vehículos, aunque el campo superior de identificación está rotulado como NIT. Para persona natural ese campo requiere validación manual y no se cambia la maestra.

## 6. Allianz Vida Grupo

### Estructura comprobada

- Libro XLSX con una hoja visible `FORMATO`, rango usado `A1:P93`.
- 165 celdas pobladas, 4 rangos combinados, 14 fórmulas y 16 validaciones.
- Hoja protegida; no se desactiva la protección en la copia.
- Sin comentarios ni hipervínculos observados.
- Las fórmulas incluyen fecha del día y controles funcionales para procedimiento, clase, tamaño del grupo, comisión, valores asegurados y condiciones. Ninguna celda con fórmula forma parte del mapping de escritura.
- Capacidad: una solicitud colectiva por copia; no es una tabla repetitiva de asegurados.

La maestra contenía valores históricos en celdas de captura. La copia generada limpia la allowlist cerrada de celdas editables antes de precargar datos confirmados. Nunca se distribuye la maestra con esos valores y el archivo fuente permanece byte a byte intacto.

### Precarga y trabajo manual

| Campo destino | Posición | Origen | Modo | Obligatorio según maestra | Observación |
|---|---|---|---|---|---|
| Nombre del tomador | B10 | `PolicyDetail.holder` | Automático | Sí | Texto exacto del Workspace |
| Identificación del tomador | B11 | documento del contacto origen | Automático | Sí | No se infiere desde otro asegurado |
| Ubicación | B13 | ciudad del contacto origen | Automático | No | Puede quedar vacía |
| Inicio de vigencia | B25 | `start_date` | Automático | No | Valor conservador del Workspace |
| Fin de vigencia | B26 | `end_date` | Automático | No | Valor conservador del Workspace |
| Compañía actual | B77 | aseguradora vigente | Automático | Condicional | No limita las aseguradoras invitadas |
| Procedimiento y clase | B5:B6 | definición del analista | Manual | Sí | No existe equivalencia confirmada |
| Actividad, grupo y demografía | B12; B17:B23 | analista/cliente | Manual | Mixto | No se aproxima desde el ramo |
| Intermediario | B30:B34 | A&S | Manual | Sí | Información interna |
| Valores asegurados y amparos | B38:B72 | decisión de cotización | Manual | Sí | No se trasladan valores sin mapping confirmado |
| Condiciones y siniestralidad | B76:B82; A85:C87 | A&S | Manual | Mixto | Requiere validación funcional |

## 7. Matriz de parametrización implementada

| Plantilla | Aseguradora | Ramo | Campo destino | Hoja | Posición | Dato origen Workspace | Transformación | Modo | Obligatorio | Observación |
|---|---|---|---|---|---|---|---|---|---|---|
| SURA Autos | SURA | 40 | Placa | Riesgos | A{fila} | `risk_attributes.placa` | texto | Automático | Sí | Deduplicado por HMAC de riesgo |
| SURA Autos | SURA | 40 | Modelo | Riesgos | B{fila} | `risk_attributes.modelo` | texto | Automático | Sí | — |
| SURA Autos | SURA | 40 | Fasecolda | Riesgos | C{fila} | No disponible en Snapshot actual | ninguna | Manual | Sí | No se inventa ni consulta Zoho |
| SURA Autos | SURA | 40 | Marca | Riesgos | D{fila} | `risk_attributes.marca` | texto | Automático | No | — |
| SURA Autos | SURA | 40 | Servicio | Riesgos | G{fila} | `risk_attributes.tipo_uso` | texto | Automático | Sí | Requiere validación de catálogo SURA |
| SURA Autos | SURA | 40 | Ciudad | Riesgos | H{fila} | `risk_attributes.ciudad` | texto | Automático | Sí | — |
| SURA Autos | SURA | 40 | Plan | Riesgos | L{fila} | No hay equivalencia SURA confirmada | ninguna | Manual | Sí | — |
| SURA Autos | SURA | 40 | Tipo ID asegurado | Riesgos | P{fila} | `insured_id_type` | texto | Automático | Sí | — |
| SURA Autos | SURA | 40 | ID asegurado | Riesgos | Q{fila} | `insured_document` cifrado en Snapshot | exacto | Automático | Sí | Nunca se registra |
| SURA Autos | SURA | 40 | Nombre asegurado | Riesgos | R{fila} | `insured_name` | texto | Automático | Condicional | Persona natural |
| SURA Autos | SURA | 40 | Razón social asegurado | Riesgos | S{fila} | No confirmada por integrante | ninguna | Manual | Condicional | Persona jurídica |
| SURA Autos | SURA | 40 | Ciudad asegurado | Riesgos | T{fila} | ciudad del origen | texto | Automático | No | — |
| Allianz Autos | Allianz | 40 | Tomador | Formato Solicitud Cotización | B5 | `PolicyDetail.holder` | texto | Automático | Sí | — |
| Allianz Autos | Allianz | 40 | Identificación tomador | Formato Solicitud Cotización | B6 | documento disponible en miembros | exacto | Automático | No | La etiqueta NIT requiere revisión para persona |
| Allianz Autos | Allianz | 40 | Inicio colectivo | Formato Solicitud Cotización | B8 | `start_date` | texto fecha conservador | Automático | No | — |
| Allianz Autos | Allianz | 40 | Aseguradora actual | Formato Solicitud Cotización | B9 | `insurer` | texto | Automático | No | No limita las invitadas |
| Allianz Autos | Allianz | 40 | Forma de pago | Formato Solicitud Cotización | B14 | `payment_mode` | allowlist de opciones conocidas | Automático | No | Si no coincide queda vacío |
| Allianz Autos | Allianz | 40 | Documento voluntario | Datos Vehículos a cotizar | A{fila} | `insured_document` | exacto | Automático | No | — |
| Allianz Autos | Allianz | 40 | Placa | Datos Vehículos a cotizar | D{fila} | `risk_attributes.placa` | texto | Automático | Sí | — |
| Allianz Autos | Allianz | 40 | Fasecolda | Datos Vehículos a cotizar | E{fila} | No disponible en Snapshot actual | ninguna | Manual | Sí | — |
| Allianz Autos | Allianz | 40 | Modelo | Datos Vehículos a cotizar | F{fila} | `risk_attributes.modelo` | texto | Automático | Sí | — |
| Allianz Autos | Allianz | 40 | Zona de circulación | Datos Vehículos a cotizar | J{fila} | `risk_attributes.ciudad` | texto | Automático | No | Confirmar semántica con negocio |
| Allianz Autos | Allianz | 40 | Uso | Datos Vehículos a cotizar | K{fila} | `risk_attributes.tipo_uso` | texto | Automático | No | La validación original permanece |
| Allianz Autos | Allianz | 40 | Relación con tomador | Datos Vehículos a cotizar | L{fila} | `relationship` | texto | Automático | No | La validación original permanece |
| SURA VG | SURA | 83 | Póliza | Plantilla Cargas Masivas | A{fila} | referencia completa de póliza | texto | Potencial automático | Sí | Generador BIFF8 desactivado |
| SURA VG | SURA | 83 | Operación / fecha efectiva | Plantilla Cargas Masivas | B:C | dato del proceso futuro | catálogo/fecha | Manual | Sí | No pertenece al Workspace actual |
| SURA VG | SURA | 83 | Identificación afiliado | Plantilla Cargas Masivas | D:E | afiliado consolidado | exacto | Potencial automático | Condicional | Requiere escritor BIFF8 seguro |
| SURA VG | SURA | 83 | Identificación y nombre asegurado | Plantilla Cargas Masivas | F:H | asegurado consolidado | exacto | Potencial automático | Sí | — |
| SURA VG | SURA | 83 | Género / nacimiento | Plantilla Cargas Masivas | I:J | no disponible en Workspace actual | ninguna | Manual | Condicional | — |
| SURA VG | SURA | 83 | Parentesco | Plantilla Cargas Masivas | K | relación confirmada | texto | Potencial automático | Condicional | — |
| SURA VG | SURA | 83 | Valores asegurados | Plantilla Cargas Masivas | L:P | valores económicos | por cobertura | Pendiente | Condicional | Falta equivalencia exacta de coberturas |
| SURA VG | SURA | 83 | Gestor a crédito | Plantilla Cargas Masivas | Q:V | datos internos/empleador | ninguna | Manual | Condicional | — |
| SURA VG | SURA | 83 | Extraprimas | Plantilla Cargas Masivas | W:AC | decisión de suscripción | ninguna | Manual | Condicional | — |
| SURA VG | SURA | 83 | Beneficiario | Plantilla Cargas Masivas | AD:AI | beneficiario consolidado + distribución | exacto/manual | Mixto | Condicional | Distribución y contingencia manuales |
| SURA VG | SURA | 83 | Datos bancarios | Plantilla Cargas Masivas | AJ:AM | no precargar | ninguna | Manual | Condicional | Dato sensible; validación A&S |
| SURA VG | SURA | 83 | Contacto | Plantilla Cargas Masivas | AN:AP | contacto presente en Snapshot | texto | Potencial automático | No | — |
| SURA VG | SURA | 83 | Diagnóstico a causa | Plantilla Cargas Masivas | AQ:AV | suscripción/A&S | ninguna | Manual | Condicional | — |
| Allianz VG | Allianz | 83 | Tomador | FORMATO | B10 | `PolicyDetail.holder` | texto | Automático | Sí | — |
| Allianz VG | Allianz | 83 | Identificación tomador | FORMATO | B11 | contacto origen del Workspace | exacto | Automático | Sí | Nunca se toma de otro integrante |
| Allianz VG | Allianz | 83 | Ubicación | FORMATO | B13 | ciudad del contacto origen | texto | Automático | No | — |
| Allianz VG | Allianz | 83 | Vigencias | FORMATO | B25:B26 | inicio y fin de póliza | texto fecha conservador | Automático | No | — |
| Allianz VG | Allianz | 83 | Compañía actual | FORMATO | B77 | aseguradora actual | texto | Automático | Condicional | No filtra la invitación |
| Allianz VG | Allianz | 83 | Procedimiento, clase, grupo y edades | FORMATO | B5:B6; B17:B23 | no confirmado | ninguna | Manual | Sí | Validar con Colectivos |
| Allianz VG | Allianz | 83 | Intermediario | FORMATO | B30:B34 | A&S | ninguna | Manual | Sí | — |
| Allianz VG | Allianz | 83 | Valores y amparos | FORMATO | B38:B72 | no confirmado | ninguna | Manual | Sí | No inventar equivalencias |
| Allianz VG | Allianz | 83 | Condiciones y siniestralidad | FORMATO | B76:B82; A85:C87 | A&S | ninguna | Manual | Condicional | — |

Los demás campos de ambas maestras permanecen manuales. Esto incluye sucursal, tipo de negocio, clave y nombre de agente, comisión, devolución, plazo, pagador, fecha de nacimiento, género, accesorios, blindaje, gas, datos técnicos no presentes, primas calculadas y observaciones.

## 8. Flujo funcional

1. La ficha de póliza muestra **Descargar plantillas de invitación**.
2. El preview valida el token firmado y restaura el Workspace local vigente.
3. Se listan las maestras del ramo, campos automáticos/manuales, capacidad y faltantes obligatorios.
4. La descarga usa POST y CSRF.
5. Si hay más de una maestra activa, genera un ZIP; un error de una maestra no contamina las demás.
6. Los nombres de archivo usan solo aseguradora y código de ramo; no contienen póliza, cliente, documento ni ID Zoho.

No se filtra por la aseguradora actual de la póliza. El ramo `40` produce SURA y Allianz.

## 9. Preservación, seguridad y límites

- Los `.xlsx` se modifican a nivel OOXML; no se cargan y rescriben con una librería que elimine partes no reconocidas.
- Todas las piezas no objetivo se copian byte a byte. Solo cambian los XML de las hojas que reciben datos y `docProps/custom.xml`, que se vacía por seguridad.
- Se conservan fórmulas, estilos, celdas combinadas, comentarios, validaciones, rangos, protección, autofiltros, print settings, relaciones y XML personalizado funcional.
- Los archivos fuente se verifican por SHA-256 antes/después en pruebas, sin documentar el hash como dato operativo.
- No se persisten archivos generados ni previews.
- Respuestas: `Cache-Control: no-store, private`; descarga con `nosniff`.
- No se registra ningún valor de celda, documento, nombre, póliza o ID.
- La capacidad física se respeta por archivo: 21 vehículos SURA y 299 vehículos Allianz. Las maestras tabulares verificadas como repetibles se dividen en archivos numerados cuando el grupo excede su capacidad; nunca se truncan. Por ejemplo, 136 vehículos en SURA producen 7 archivos completos dentro del ZIP.
- Una plantilla con campos manuales faltantes continúa disponible y los deja vacíos para diligenciamiento posterior; el preview los informa como trabajo manual no bloqueante.
- Si una maestra no ha sido verificada como repetible, exceder su capacidad sigue siendo un error de integridad y no genera una salida parcial.
- Falta QA funcional de A&S sobre equivalencias de servicio/uso, zona de circulación y el tratamiento de tomador persona natural.

## 10. Actualización de maestras

Una actualización requiere: reemplazo consciente del archivo fuente, nueva versión en el catálogo, reinspección de hojas/celdas/validaciones/metadatos, ajuste explícito de mappings y pruebas de preservación. No existe selección de archivo por request ni mapping basado en filename.
