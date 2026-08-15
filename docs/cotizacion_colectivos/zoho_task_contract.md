# Contrato dirigido de Zoho Tasks para Colectivos

## Alcance y regla de decisión

Este documento prepara una fase futura; **no habilita ni implementa escritura en Zoho**. La evidencia combina el snapshot de metadata Sandbox generado el 14 de agosto de 2026 con SDK 1.1.0, la inspección del API público instalado temporalmente fuera de `site-packages` y diagnósticos dirigidos ejecutados mediante `integrations.zoho.get_zoho`.

Etiquetas de confianza:

- `CONFIRMADO_METADATA`: existe en el snapshot, pero no fue contrastado con un registro poblado.
- `CONFIRMADO_DATOS`: observado en una muestra Sandbox sanitizada.
- `CONFIRMADO_CÓDIGO_EXISTENTE`: garantía comprobable en el repositorio o SDK 1.1.0.
- `INFERIDO_NO_IMPLEMENTAR`: diseño plausible que requiere evidencia antes de codificar.
- `PENDIENTE_A&S`: decisión o dato que A&S debe confirmar.

El snapshot tiene estado `partial`: 89 módulos, Fields API disponible para 63, 26 fallos, cero layouts y cero related lists. Por ello no se atribuye obligatoriedad por layout ni comportamiento de listas relacionadas que la fuente no demostró.

## Resultado de la exploración dirigida

| Pregunta | Evidencia | Resultado | Confianza |
|---|---|---|---|
| ¿Existe `Tasks`? | `modules.json` y `fields.json` | Sí; módulo API `Tasks`, etiqueta Tareas. | `CONFIRMADO_METADATA` |
| ¿Existen los tres tipos? | `picklists.json`, `Tasks.tipo_de_solicitud` | Valores activos exactos `Ingresos`, `Retiros` y `Cotización`. | `CONFIRMADO_METADATA` |
| ¿Hay una muestra Sandbox de cada tipo? | Tres búsquedas exactas, página 1, límite 1 | No se obtuvo ninguna muestra. No se concluye que no existan en otros entornos. | `CONFIRMADO_DATOS` |
| ¿Dónde aparece Fonconstruimos? | `Tasks.Vendedor` | Fue observado como valor utilizado por ese picklist. No se encontró una Task representativa en la muestra dirigida: esto no prueba que sea usuario, contacto, empresa ni relación de pertenencia. | `CONFIRMADO_DATOS` |
| ¿Existe como empresa en `Contacts`? | Búsquedas exactas por `Nombre_comercial`, `Raz_n_social` y `Full_Name` | Sin coincidencias; no hay record ID que documentar. | `CONFIRMADO_DATOS` |
| ¿Existe como `Accounts`? | Fields API y listado mínimo por la fachada | Inconcluso: Fields API devolvió `authorization` y el listado mínimo devolvió `SDKException`, normalizada como categoría `sdk`. No se usó HTTP directo ni otro perfil. | `PENDIENTE_A&S` |
| ¿Los lookups de `Contacts` resuelven el fondo? | `Grupo_econ_mico` y `Empresa`, ambos lookup según metadata; búsquedas exactas dirigidas | Cero coincidencias. No se obtuvo un Contact que demostrara fondo ni empresa. | `CONFIRMADO_DATOS` |
| ¿Los lookups de `Polizas` resuelven el fondo? | `Tomador_principal1`, `Grupo_econ_mico` y `Grupo_empresarial_ARL`, todos lookup; además `Vendedor` picklist | Cero coincidencias exactas en los cuatro campos. | `CONFIRMADO_DATOS` |
| ¿Aparece en `Riesgos1`? | `Tomador` y `Subgrupo`, ambos texto según metadata | `Tomador = Fonconstruimos` produjo tres registros; `Subgrupo` produjo cero. Uno de los tres riesgos tiene lookup `Asegurado`, pero ninguno presentó `P_liza` o `Subgrupo` poblado en la muestra. | `CONFIRMADO_DATOS` |
| ¿El asegurado permite resolver una empresa? | Relación ya confirmada en código `Riesgos1.Asegurado → Contacts` | Se intentó leer el único Contact enlazado; el SDK oficial devolvió `SDKException` en `get_record`. No se pudo observar `Empresa` ni `Grupo_econ_mico`. | `PENDIENTE_A&S` |
| ¿Está demostrada la relación Fonconstruimos → empresas? | Conjunto de evidencia anterior | No. `Riesgos1.Tomador` aporta una pista por texto, no una lista estructural ni exhaustiva de empresas pertenecientes al fondo. | `PENDIENTE_A&S` |
| ¿Cómo resuelven `What_Id` y `Who_Id`? | Metadata más muestra dirigida | Ambos campos existen, pero al no haber Tasks de muestra no se obtuvo módulo real ni huella de ID. | `PENDIENTE_A&S` |
| ¿Cómo se relaciona con póliza/tomador/asegurado/`Riesgos1`? | Campos auxiliares de Tasks | Existen `N_mero_p_liza`, `ID_Tomador`, `ID_asegurado` e `ID_Riesgos1_task`; su semántica y precedencia frente a lookups no quedaron demostradas por datos. | `INFERIDO_NO_IMPLEMENTAR` |

No se almacenaron respuestas, nombres ni IDs crudos. El diagnóstico genera únicamente huellas `sha256` truncadas. No se obtuvo un registro principal ni record ID de Fonconstruimos. Las búsquedas actuales tampoco obtuvieron Tasks representativas.

La evidencia `Tasks.Vendedor = Fonconstruimos` debe conservarse únicamente como `CONFIRMADO_DATOS`: el valor aparece en ese campo. Permanecen **no demostrados** el módulo principal, el record ID del fondo, la relación fondo → empresas y la relación de una futura selección con Tasks. No debe usarse ese picklist para poblar ni validar el formulario especial.

## Campos candidatos del payload futuro

| API name | Etiqueta / tipo | Obligatoriedad demostrada | Uso futuro propuesto | Confianza |
|---|---|---|---|---|
| `Subject` | Asunto / text | `system_mandatory=true` | Asunto operacional sin PII innecesaria. | `CONFIRMADO_METADATA` |
| `tipo_de_solicitud` | Tipo de solicitud / picklist | No obligatorio en Fields API | `Ingresos`, `Retiros` o `Cotización`, según evento local. | `CONFIRMADO_METADATA` |
| `What_Id` | Relacionado con / lookup | No obligatorio en Fields API | Probable relación principal; módulo e ID pendientes. | `INFERIDO_NO_IMPLEMENTAR` |
| `Who_Id` | Nombre de contacto / lookup | No obligatorio en Fields API | Probable contacto; confirmar si aplica a empresa, tomador o solicitante. | `INFERIDO_NO_IMPLEMENTAR` |
| `Owner` | Titular de la tarea / ownerlookup | No obligatorio en Fields API | Asignación mediante ID, nunca por nombre libre. | `CONFIRMADO_METADATA` |
| `Responsable` | Responsable tarea / picklist | No obligatorio en Fields API | Hay 60 valores activos; A&S debe definir el valor por flujo. | `PENDIENTE_A&S` |
| `Status` | Estado / picklist | No obligatorio en Fields API | A&S debe elegir valor inicial. Metadata ofrece 21 valores y separa valor API de etiqueta visible en algunos casos. | `PENDIENTE_A&S` |
| `rea` | Área / picklist | No obligatorio en Fields API | No existe valor `Colectivos`; los valores incluyen áreas de negocio, `SOAT`, `Cartera` y `Administrativo`. No inventar uno. | `PENDIENTE_A&S` |
| `Due_Date` | Fecha de vencimiento / date | No obligatorio en Fields API | Calcular sólo después de definir calendario/SLA; no confundir con vigencia del enlace externo de 48 h. | `INFERIDO_NO_IMPLEMENTAR` |
| `Fecha_de_solicitud_del_cliente` | Fecha solicitud cliente / date | No obligatorio en Fields API | Fecha de entrada del requerimiento, si A&S confirma la semántica. | `INFERIDO_NO_IMPLEMENTAR` |
| `Fecha_y_hora_vencimiento` | Fecha y hora vencimiento / datetime | No obligatorio en Fields API | Resolver si sustituye o complementa `Due_Date`. | `PENDIENTE_A&S` |
| `Correo_del_solicitante` | Correo del solicitante / email | No obligatorio en Fields API | Minimizar y enviar sólo si el proceso lo exige. | `CONFIRMADO_METADATA` |
| `N_mero_p_liza` | Número póliza / text | No obligatorio en Fields API | Texto desnormalizado; no usar como prueba de relación sin resolver el contrato. | `CONFIRMADO_METADATA` |
| `ID_Tomador` | ID Tomador / text | No obligatorio en Fields API | Campo auxiliar; módulo y formato pendientes. | `PENDIENTE_A&S` |
| `ID_asegurado` | ID Asegurado / text | No obligatorio en Fields API | Campo auxiliar; posible contacto o asegurado, pendiente. | `PENDIENTE_A&S` |
| `ID_Riesgos1_task` | ID_asegurado / text | No obligatorio en Fields API | El nombre API y la etiqueta divergen; no mapear hasta obtener muestra. | `PENDIENTE_A&S` |

La metadata sólo demuestra `Subject` como obligatorio del sistema. Un layout, blueprint, función o regla del CRM puede imponer más restricciones; el snapshot no obtuvo layouts y las búsquedas no produjeron muestras. Un payload mínimo no debe implementarse todavía.

## Publicador local implementado; transporte remoto bloqueado

`ColectivosTaskOutbox` ya registra origen (solicitud o cotización), evento,
versión, clave idempotente única, payload cifrado, checksum, estado, intentos y
un ID remoto cifrado opcional. `enqueue_task()` retorna la misma fila para el
mismo `(origen, evento, versión)` y rechaza una colisión con payload distinto.

El builder permite exclusivamente `Subject` y `tipo_de_solicitud`. Los valores
exactos son `Ingresos`, `Retiros` y `Cotización`. El dry-run devuelve módulo,
campos, tipo, longitud del asunto, presencia de adjuntos y `writes=0`, sin PII.

El transporte remoto continúa bloqueado en capas:

1. `COLECTIVOS_TASK_PUBLISH_ENABLED` está fijado en `False`.
2. El perfil debe ser exactamente `sandbox`; Production se rechaza aun con una
   configuración externa errónea.
3. Se exige una confirmación explícita adicional `SANDBOX_TASK_WRITE`.
4. Aun superando lo anterior, el publicador levanta `TaskContractIncomplete`
   porque layouts y reglas obligatorias no están demostrados.

Por ello esta intervención hizo 0 CREATE, 0 UPDATE, 0 UPSERT y 0
read-after-write. No existe Task ID de prueba. Habilitar un flag no basta para
saltar la guarda contractual.

Este patrón evita prometer atomicidad distribuida inexistente entre PostgreSQL y Zoho. La compensación es operacional y auditable, no un “rollback remoto” ficticio.

## Adjuntos y orden futuro

El snapshot confirma un módulo `Attachments` soportado por API, oculto en sistema, pero no obtuvo related lists. La inspección del API público de `ays-zoho-sdk` 1.1.0 confirma que `ZohoFacade` expone organization, metadata, records, search y COQL; **no expone métodos públicos para listar, descargar, subir o eliminar adjuntos**. No se inventa un nombre de método ni una URL.

Cuando exista una API pública aprobada, el orden seguro sería:

1. publicar/reconciliar la Task idempotente;
2. registrar cada adjunto local con hash, tamaño, MIME y estado;
3. subir cada archivo una sola vez y persistir su ID remoto;
4. reintentar únicamente adjuntos pendientes;
5. mantener la Task en estado local `attachments_pending` si una carga falla, sin duplicar la Task;
6. no borrar automáticamente una Task válida por un fallo de adjunto; cualquier compensación destructiva requiere regla de negocio y auditoría.

Hasta que el SDK publique esa superficie, los adjuntos de Colectivos permanecen locales y cifrados según la implementación actual. `has_attachments` en el contrato local es informativo; no activa integración remota.

## Outlook, correo y OTP

La aplicación actual puede enviar correo mediante el backend Django configurado y conserva enlaces externos de 48 horas. Eso no equivale a compartir “desde Outlook”. Sin una integración Microsoft Graph/Outlook autorizada no es posible crear borradores en el buzón del usuario, enviar como ese usuario, adjuntar desde su mailbox, conservar conversation ID ni confirmar entrega desde Outlook. Un enlace `mailto:` sólo puede prellenar texto de forma limitada y no garantiza adjuntos, envío ni auditoría.

Una fase futura necesitaría registro de aplicación Microsoft, permisos mínimos revisados (por ejemplo, el alcance de envío que A&S apruebe), consentimiento, almacenamiento seguro de tokens, selección explícita de remitente, creación de draft, carga de adjuntos, envío y auditoría por ID de mensaje. Nada de esto se habilitó aquí.

OTP está activo para Novedades y Cotización Individual. El token por sí solo no
autoriza el formulario; se usa hash, vencimiento, máximo de intentos y cookie
firmada aislada. No sustituye la autenticación de Zoho.

El expediente interno muestra el estado de la outbox/Task como información
operativa. Mientras layouts y reglas obligatorias sigan incompletos, la acción
se presenta deshabilitada y explicada; no existe un endpoint que finja publicar.

Los accesos vencen exactamente 172800 segundos después de crearse por defecto,
sin redondeo a fin de día.

## Fonconstruimos en el formulario vigente

La falta de catálogo estructurado ya no bloquea la captura: cuando el contexto
corresponde a “Fondo de Empleados Construimos Sueños” / Fonconstruimos, el
formulario exige `Empresa a la cual pertenece` como texto editable, sanitizado
y validado en servidor. Se cifra junto con la respuesta y nunca se interpreta
como record ID o lookup. Un selector sólo podrá reemplazarlo cuando A&S demuestre
y autorice la relación real y su validación backend.

## Discovery dirigido de Fonconstruimos

La intervención específica ejecutó 15 operaciones lógicas de lectura Sandbox, todas por la fachada existente:

- fase estructural: 12 intentos: 1 organización, 1 metadata de `Accounts`, 1 listado mínimo de `Accounts`, 2 búsquedas `Contacts`, 4 `Polizas`, 2 `Riesgos1` y 1 `Tasks`;
- fase de resolución: 3 intentos: 1 organización, 1 búsqueda `Riesgos1.Tomador` y 1 lectura del `Contacts` enlazado;
- resultados no exitosos: `Accounts` metadata=`authorization`, `Accounts` listado=`sdk` (`SDKException`) y `Contacts.get_by_id`=`sdk` (`SDKException`);
- 0 operaciones de escritura, 0 Production y 0 respuestas crudas persistidas.

El error de `Accounts` anterior no fue un criterio de búsqueda inválido: en esta ejecución no se buscó por `Account_Name`. La Fields API fue rechazada por autorización y hasta un listado de un único `id` falló dentro del SDK. Con la evidencia disponible sólo puede clasificarse como acceso de metadata no autorizado y fallo SDK en records; no se atribuye una causa más específica sin inventarla.

### Revisión manual necesaria

Como no quedó demostrada una relación estructurada, antes de implementar el selector A&S debe:

1. aportar una empresa real que se sepa que pertenece a Fonconstruimos y abrir su registro en Zoho Sandbox;
2. confirmar el módulo y record ID del registro de esa empresa y revisar sus campos/related lists de grupo económico, empresa, tomador, pólizas y riesgos;
3. abrir los tres `Riesgos1` cuyo `Tomador` es exactamente `Fonconstruimos`; en el único que tiene `Asegurado`, abrir ese Contact y comprobar `Empresa` y `Grupo_econ_mico`;
4. confirmar si `Riesgos1.Tomador` es una relación autorizada de pertenencia o sólo texto descriptivo;
5. revisar permisos/API de `Accounts` si ese es el módulo corporativo esperado, pues actualmente Fields API devuelve autorización y records no es legible por el SDK.

El formulario especial permanece sin selector definitivo. Su diseño futuro deberá resolver empresas desde una relación autorizada, firmar el contexto y volver a validar en backend que el ID recibido pertenece al fondo; nunca aceptará un ID arbitrario ni una lista hardcodeada.

## Evidencia operativa histórica de Tasks y pendientes

Se realizaron dos ejecuciones controladas:

- ejecución 1: 7 operaciones lógicas de lectura exitosas: 1 `organization.get`, 3 `search.Contacts`, 3 `search.Tasks`;
- ejecución 2: 5 operaciones lógicas intentadas antes del corte: 1 `organization.get`, 3 `search.Contacts`, 1 `search.Accounts` que terminó en categoría `sdk`;
- total: 12 operaciones lógicas de lectura intentadas, 11 completadas y 1 fallida; 0 Production, 0 create/update/upsert, 0 adjuntos, 0 respuestas crudas persistidas.

Pendientes antes de implementar escritura:

- aportar en Sandbox al menos una Task representativa por tipo o IDs de muestra autorizados;
- confirmar si Fonconstruimos es picklist, usuario, empresa o combinación, y aportar el módulo/ID correcto;
- resolver módulos reales de `What_Id`, `Who_Id`, `ID_Tomador`, `ID_asegurado` e `ID_Riesgos1_task`;
- definir `Owner`, `Responsable`, estado inicial, área y SLA;
- obtener reglas de layout/blueprint y obligatoriedad operacional;
- definir estrategia pública de adjuntos en una versión futura del SDK;
- aprobar, si se desea, el alcance funcional y de permisos de Outlook/Graph.
