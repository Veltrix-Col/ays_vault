# Búsquedas y fichas funcionales de Cotización – Colectivos

## 1. Objetivo

Definir el comportamiento implementado para consultar empresas, individuos y
sus relaciones confirmadas en el perfil Zoho configurado. La aplicación es de solo
lectura, no replica registros y no permite seleccionar perfiles desde el
navegador.

El documento `Modelo_Datos_Zoho_CRM_AYS_Levantamiento.docx` no estaba presente
en el repositorio ni en la carpeta de trabajo durante esta intervención. No se
sobrescribió ni se reconstruyó. Este Markdown reúne los cambios para una
incorporación posterior controlada al documento maestro.

## 2. Modelo utilizado

- Perfil funcional global: `ZOHO_ACTIVE_PROFILE`, con `sandbox` como valor
  predeterminado y `production` como única alternativa permitida.
- Fachada única resuelta internamente con `get_zoho(profile=perfil)`; el perfil
  nunca procede del navegador ni de un override por aplicación.
- Personas y empresas: módulo `Contacts`.
- Empresas: `Tipo_de_persona = Persona jurídica` y `Tipo_ID = NIT`.
- Individuos: `Tipo_de_persona = Persona natural` y `Tipo_ID = CC`.
- Documento: `N_mero_de_ID`.
- Empresa: nombre principal `Nombre_comercial`, fallback `Raz_n_social` y
  fallback técnico final `Full_Name` únicamente para búsqueda.
- Individuo: `Full_Name`; `First_Name` y `Last_Name` complementan la búsqueda.

## 3. Buscador de empresas

El campo visible es **NIT o nombre de empresa**.

Para una entrada numérica de al menos tres dígitos se ejecuta primero una
comparación exacta sobre `N_mero_de_ID`. Si esa consulta devuelve un resultado
válido, no se ejecuta el prefijo; en caso contrario se consulta `starts_with`.
Los criterios remotos conservan obligatoriamente
`Tipo_de_persona = Persona jurídica`, pero no incluyen `Tipo_ID = NIT`: el
diagnóstico de Producción confirmó que Search API descartaba registros válidos
al combinar ese picklist, aunque el registro devuelto por documento contenía
`Tipo_ID = NIT`.

Después de cada respuesta se aplica una validación local defensiva. Se rechaza
cualquier registro que no sea `Persona jurídica` y cualquier `Tipo_ID`
poblado distinto de `NIT`. Un `Tipo_ID` vacío no se descarta automáticamente:
se admite únicamente si el tipo de persona confirma que es jurídica, para no
ocultar registros con clasificación documental incompleta. Esta decisión no
amplía resultados a personas naturales.

Para texto de al menos tres caracteres se consulta primero `equals` y después
`starts_with` sobre `Nombre_comercial`, `Raz_n_social` y `Full_Name`. El QA real
del Sandbox confirmó que `starts_with` es la operación estable para el prefijo
de nombre; `contains` fue rechazado por la API y no se utiliza. Los máximos 20
candidatos se ordenan localmente para presentar primero la igualdad textual
exacta y después el prefijo. El usuario no aporta COQL, campos, módulos ni
comodines.

## 4. Buscador de individuos

El campo visible es **Cédula o nombre del individuo**.

Para una entrada numérica de al menos tres dígitos se prioriza igualdad exacta
y después prefijo en `N_mero_de_ID`, siempre con persona natural y CC. Para
texto se consulta igualdad y luego coincidencia parcial sobre `Full_Name`,
`First_Name` y `Last_Name`.

## 5. Orden, deduplicación y límites

El orden es determinista por fase: exacto antes de prefijo o coincidencia
parcial. Los registros se deduplican en memoria exclusivamente por ID técnico,
sin conservar respuestas originales. Se devuelven como máximo 20 resultados.
No se descarga el módulo completo ni se filtra la totalidad de Contacts en
Python.

Los documentos se muestran enmascarados. Los IDs de Zoho no se renderizan: las
fichas usan tokens firmados, opacos, ligados al tipo de entidad y con expiración
de 15 minutos.

## 6. Campos de ficha confirmados

### Contacto

- `Tipo_de_persona`
- `Tipo_ID`
- `N_mero_de_ID`
- `Nombre_comercial`
- `Raz_n_social`
- `Full_Name`
- `First_Name`
- `Last_Name`
- `Estado`
- `Email`
- `Phone`
- `Mobile`
- `Direcci_n`
- `Ciudad_de_direcci_n_principal`
- `Empresa`

Los campos vacíos de contacto no crean bloques vacíos. `Empresa` solo se
muestra si Zoho devuelve un lookup poblado; no se infiere por nombre.

### Pólizas

- `Name`
- `Tomador_principal1`
- `Estado_de_la_p_liza`
- `Ramo`
- `Aseguradora1`
- `P_liza_Fecha_de_inicio_vigencia`
- `P_liza_Fecha_fin_de_la_vigencia`
- `Layout`

`Layout` se clasifica como colectivo, otro o desconocido. Las pólizas colectivas
se presentan primero, sin excluir registros con layout ausente.

### Asegurados y riesgos

En `Riesgos1` se usan `Name`, `P_liza`, `Asegurado`, `Riesgo`, `Estado`, `Ramo`,
`Aseguradora`, `Fecha_ingreso_riesgo` y `Fecha_salida_riesgo`.

En `Riesgos` se usan `Name`, `Tipo_de_riesgo`, `Fecha_inicio`, `Fecha_fin` y
`Layout`. El resumen es neutral y tolera campos ausentes.

## 7. Relaciones

Confirmadas e implementadas:

- `Riesgos1.Asegurado → Contacts`.
- `Riesgos1.P_liza → Polizas`.
- `Riesgos1.Riesgo → Riesgos`.

La ruta principal es `Contact → Riesgos1 → P_liza/Riesgo`. Pólizas y riesgos se
deduplican por ID y nunca por nombre.

Relación parcial:

- `Polizas.Tomador_principal1 → Contacts`.

Se consulta mediante igualdad exacta del lookup, pero los resultados aparecen
en una subsección independiente **Pólizas como tomador — relación en
validación**. No se mezclan con la ruta confirmada.

No confirmadas y no inferidas:

- Empresa → individuos.
- Individuo → empresa cuando `Contacts.Empresa` está vacío.
- Contacto → riesgo directo mediante contratante o contratista.

## 8. Seguridad y privacidad

- Acceso heredado desde la intranet, separado de la autenticación de Vault.
- En desarrollo local explícito no requiere un segundo login ni MFA.
- En producción requiere el futuro validador delegado aprobado y falla cerrado
  mientras no exista esa especificación.
- CSRF en búsquedas POST.
- Respuestas de detalle con `no-cache`.
- Perfil explícito `sandbox` o `production`, sin fallback entre ambientes y con
  validación de Organization API antes de la consulta funcional.
- Módulos y campos constantes.
- Sin COQL suministrado por el usuario.
- Sin métodos de escritura.
- Sin modelos, migraciones, caché persistente o historial de búsquedas.
- Logs sin documento, nombre, correo, teléfono, término completo, ID Zoho,
  cuerpo, token o header.

Los logs contienen solo entidad, duración, cantidad de resultados, categoría
de error, ID interno del usuario, perfil y correlación aleatoria.

La duración de la validación de Organization y la de cada llamada Search API se
registran por separado. Queda pendiente evaluar una caché breve y acotada para
la validación del ambiente; no se implementó en esta intervención para evitar
alterar el modelo de seguridad o introducir estado compartido sin diseño
específico.

## 9. Manejo de errores

Los errores principales se traducen a mensajes funcionales. Un fallo al cargar
asegurados, una póliza, un riesgo o la relación directa de tomador no impide
mostrar la identificación básica del contacto. La sección afectada indica
indisponibilidad temporal sin revelar detalles técnicos.

## 10. QA manual

1. Iniciar el servidor con el entorno estable.
2. En local, abrir directamente sin iniciar sesión en CardManager.
3. Abrir `/cotizacion-colectivos/` y comprobar el badge correspondiente:
   `Sandbox · Solo lectura` o `Producción · Solo lectura`.
4. Buscar una empresa conocida por NIT exacto, prefijo de tres dígitos y nombre
   parcial, sin registrar los valores.
5. Buscar un individuo conocido por CC exacta, prefijo y nombre parcial.
6. Confirmar documentos enmascarados y ausencia de IDs técnicos.
7. Abrir fichas y navegar por Resumen, Pólizas, Asegurados, Riesgos e
   Información pendiente.
8. Confirmar que las pólizas como tomador permanecen separadas y marcadas en
   validación.
9. Revisar 320, 375, 768, 1024 y 1440 px sin scroll horizontal.
10. Confirmar en logs únicamente métricas saneadas y ausencia de operaciones de
    escritura.

## 11. Límites y pendientes

- El permiso definitivo por aplicación sigue pendiente; no se reutilizan roles
  de CardManager.
- No se implementaron cotización, renovación, modificación ni formatos.
- No se muestran campos de gestión comercial, facturación, representante legal
  o grupo económico hasta confirmar necesidad, permisos y comportamiento real.
- No se implementa relación empresa–individuo sin cobertura de `Empresa`.
- Los indicadores de relaciones no se calculan en la lista de búsqueda para
  evitar consultas N+1; aparecen en la ficha consolidada.

## 12. Cambio de ambiente

La transición a Producción requiere autorización operativa, configuración
productiva completa, validación del modelo y pruebas de privacidad. Se realiza
con `ZOHO_ACTIVE_PROFILE=production` y reinicio; el rollback usa
`sandbox` y reinicio. No existe selección automática ni fallback. El detalle
operativo está en `environment_profiles.md`.
