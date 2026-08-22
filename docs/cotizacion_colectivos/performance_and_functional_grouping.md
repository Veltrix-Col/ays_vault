# Preparación reutilizable y agrupación funcional

## Diagnóstico

El tiempo percibido al generar un acceso no provenía principalmente del ORM. La ficha ya había consultado `Polizas`, `Riesgos1`, los contactos relacionados y `Riesgos`, pero la acción de creación volvía a construir el mismo DTO. En un cache miss de Organization, las llamadas remotas observadas sumaban varios segundos; los errores del SDK en `get_by_id` podían agregar otra llamada de búsqueda mediante el fallback de la fachada. Finalmente, el snapshot se materializaba registro por registro.

## Flujo actual

```text
Zoho (solo lectura)
  → PolicyService
  → PolicyDetail + GroupMember
  → preparación cifrada y versionada (TTL)
  → snapshot operacional Django
  → acceso externo
```

Al abrir una póliza se construye la preparación. Al generar el acceso, `PolicyService` valida perfil, backend, entidad origen, póliza, ramo, versión, checksum y expiración. Un hit no inicializa la fachada ni consulta Organization API. Si ya existe una solicitud activa compatible, se reutiliza antes de reconstruir el detalle y se genera un acceso nuevo, revocando el anterior cuando corresponde.

La variable `COLECTIVOS_POLICY_PREPARATION_TTL_SECONDS` controla el TTL; su valor predeterminado es 600 segundos. El botón **Actualizar datos desde Zoho** invalida el valor anterior mediante POST con CSRF y lo reconstruye sin crear solicitudes ni escribir en Zoho.

El payload de cache se serializa como JSON, se cifra con la llave de campos del proyecto y lleva checksum. La clave de cache usa HMAC; no contiene IDs Zoho ni datos personales. `LocMemCache` sirve para desarrollo y pruebas. Un despliegue con varios workers debe configurar un backend compartido, preferiblemente Redis, para que la preparación sea reutilizable entre procesos. El mecanismo falla cerradamente ante alteración, versión incompatible, perfil distinto o expiración.

Los registros operacionales se crean con `bulk_create(batch_size=500)` dentro de la transacción del expediente. Los logs saneados separan Organization, fachada, consultas, agrupación, validación/serialización del snapshot, inserción masiva, creación del acceso y total. Los errores remotos registran solo clase/código técnico seguro, operación, backend y fallback; nunca cuerpos, criterios, IDs o datos del cliente.

## Agrupación funcional

El snapshot conserva referencias HMAC independientes para afiliado, asegurado, beneficiario y riesgo. La interfaz consolida exclusivamente por esas referencias, nunca por nombre, documento o coincidencia textual. Una persona que aparece en varias filas de `Riesgos1` se presenta una vez y acumula sus roles y las referencias técnicas de origen.

- Salud y Exequial: principal, miembros, beneficiarios y parentescos.
- Vida grupo deudores: asegurado principal, relaciones y valor/riesgo disponible.
- Hogar: tarjeta principal por referencia de inmueble.
- Movilidad: tarjeta principal por referencia de vehículo.

Una acción funcional puede apuntar a varias filas técnicas. Se guarda una sola novedad y un payload cifrado con `source_record_keys`; no se exponen PK locales ni IDs Zoho. Referencias contradictorias o beneficiarios sin principal generan advertencias internas y no se unen silenciosamente.

La plantilla Excel versión 3 también consolida roles. Incluye Principal, Rol principal, Persona relacionada, Roles, Tipo de relación y Referencia funcional. La hoja oculta mantiene el mapa a UUID operacionales y su contenido forma parte de la firma del libro. Al importar se valida la firma, la póliza, la pertenencia de todas las filas y la ausencia de referencias duplicadas.

## Límites y QA pendiente

Los objetivos de menos de un segundo con hit y de hasta diez segundos con miss deben confirmarse en Production mediante el QA controlado solicitado; las pruebas automatizadas no sustituyen esa medición. También queda pendiente medir `sdk_lock_wait_ms`, `sdk_operation_ms` y `rest_fallback_ms` porque la fachada actual no expone esos tiempos por operación al consumidor. No se modificó `integrations/zoho` para inventar métricas.

Casos manuales autorizados: individuo con hija, empresa con beneficiarios, Hogar con inmuebles y Movilidad con vehículos. Para cada uno se debe abrir la ficha, generar dos accesos consecutivos, verificar hit/miss en logs, abrir el portal, guardar una novedad y revisar el comparativo en 320, 375, 768, 1024 y 1440 px.
