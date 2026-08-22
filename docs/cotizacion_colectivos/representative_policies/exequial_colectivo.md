# Exequial colectivo

## Identificación técnica

- Póliza autorizada: `158140`.
- Perfil: `production`; backend: `sdk`; solo lectura.
- Layout: configured; ramo: Exequial colectivo; aseguradora: Prever Previsión General S.A.S.
- Estado: Vigente; vigencia inicio/fin: date_present / date_present.
- Tomador: estructura `id_and_name`; no se conserva ni publica su valor.

## Campos poblados de Polizas

| Label | API name | Tipo | Poblado | Cobertura | Cliente ve | Cliente edita | A&S edita | Origen |
|---|---|---|---:|---:|---|---|---|---|
| Póliza | `Name` | text | 1 | 100.0% | enmascarado | no | sí | Zoho/A&S |
| Póliza Propietario | `Owner` | ownerlookup | 1 | 100.0% | sí | no | sí | A&S |
| Correo electrónico | `Email` | email | 1 | 100.0% | enmascarado | no | sí | Zoho/A&S |
| Creado por | `Created_By` | ownerlookup | 1 | 100.0% | sí | no | no | Sistema |
| Modificado por | `Modified_By` | ownerlookup | 1 | 100.0% | sí | no | no | Sistema |
| Hora de creación | `Created_Time` | datetime | 1 | 100.0% | sí | no | no | Sistema |
| Hora de modificación | `Modified_Time` | datetime | 1 | 100.0% | sí | no | no | Sistema |
| Hora de la última actividad | `Last_Activity_Time` | datetime | 1 | 100.0% | sí | no | no | Sistema |
| Moneda | `Currency` | picklist | 1 | 100.0% | sí | no | sí | Zoho/A&S |
| Tasa de cambio | `Exchange_Rate` | double | 1 | 100.0% | sí | no | no | Sistema |
| No participación del correo electrónico | `Email_Opt_Out` | boolean | 1 | 100.0% | enmascarado | no | no | Sistema |
| Diseño | `Layout` | layout | 1 | 100.0% | sí | no | no | Sistema |
| ID de registro | `id` | bigint | 1 | 100.0% | sí | no | no | Sistema |
| Locked | `Locked__s` | boolean | 1 | 100.0% | sí | no | no | Sistema |
| 12 | `Pago_12` | currency | 1 | 100.0% | sí | pendiente | sí | Cliente/A&S |
| Fecha 11 | `Fecha_11` | date | 1 | 100.0% | sí | no | sí | Zoho/A&S |
| 2 | `Pago_2` | currency | 1 | 100.0% | sí | pendiente | sí | Cliente/A&S |
| Fecha 1 | `Fecha_1` | date | 1 | 100.0% | sí | no | sí | Zoho/A&S |
| Fecha 12 | `Fecha_12` | date | 1 | 100.0% | sí | no | sí | Zoho/A&S |
| 3 | `Pago_3` | currency | 1 | 100.0% | sí | pendiente | sí | Cliente/A&S |
| Fecha 2 | `Fecha_2` | date | 1 | 100.0% | sí | no | sí | Zoho/A&S |
| 4 | `Pago_4` | currency | 1 | 100.0% | sí | pendiente | sí | Cliente/A&S |
| Fecha 3 | `Fecha_3` | date | 1 | 100.0% | sí | no | sí | Zoho/A&S |
| 5 | `Pago_5` | currency | 1 | 100.0% | sí | pendiente | sí | Cliente/A&S |
| Fecha 4 | `Fecha_4` | date | 1 | 100.0% | sí | no | sí | Zoho/A&S |
| 6 | `Pago_6` | currency | 1 | 100.0% | sí | pendiente | sí | Cliente/A&S |
| Fecha 5 | `Fecha_5` | date | 1 | 100.0% | sí | no | sí | Zoho/A&S |
| Fecha 6 | `Fecha_6` | date | 1 | 100.0% | sí | no | sí | Zoho/A&S |
| Fecha 7 | `Fecha_7` | date | 1 | 100.0% | sí | no | sí | Zoho/A&S |
| 7 | `Pago_7` | currency | 1 | 100.0% | sí | pendiente | sí | Cliente/A&S |
| Fecha 8 | `Fecha_8` | date | 1 | 100.0% | sí | no | sí | Zoho/A&S |
| 8 | `Pago_8` | currency | 1 | 100.0% | sí | pendiente | sí | Cliente/A&S |
| Fecha 9 | `Fecha_9` | date | 1 | 100.0% | sí | no | sí | Zoho/A&S |
| 9 | `Pago_9` | currency | 1 | 100.0% | sí | pendiente | sí | Cliente/A&S |
| Fecha 10 | `Fecha_10` | date | 1 | 100.0% | sí | no | sí | Zoho/A&S |
| 10 | `Pago_10` | currency | 1 | 100.0% | sí | pendiente | sí | Cliente/A&S |
| 11 | `Pago_11` | currency | 1 | 100.0% | sí | pendiente | sí | Cliente/A&S |
| 1 | `Pago_1` | currency | 1 | 100.0% | sí | pendiente | sí | Cliente/A&S |
| Forma de pago | `Modo_de_pago` | picklist | 1 | 100.0% | sí | pendiente | sí | Cliente/A&S |
| Periodicidad | `Frecuencia` | picklist | 1 | 100.0% | sí | no | sí | Zoho/A&S |
| TOT | `TOTAL` | formula | 1 | 100.0% | sí | no | no | Sistema |
| Tomador | `Tomador_principal1` | lookup | 1 | 100.0% | sí | no | sí | Zoho/A&S |
| Ramo | `Ramo` | picklist | 1 | 100.0% | sí | no | sí | Zoho/A&S |
| Líder Comercial | `L_der_Comercial` | picklist | 1 | 100.0% | sí | no | sí | Zoho/A&S |
| Fecha creación | `Fecha_de_creaci_n` | date | 1 | 100.0% | sí | no | sí | Zoho/A&S |
| % comisión | `comisi_n` | percent | 1 | 100.0% | sí | no | sí | Zoho/A&S |
| % IVA | `IVA` | percent | 1 | 100.0% | sí | no | sí | Zoho/A&S |
| Póliza - Fecha inicio vigencia | `P_liza_Fecha_de_inicio_vigencia` | date | 1 | 100.0% | sí | no | sí | Zoho/A&S |
| % Participación | `Participaci_n` | percent | 1 | 100.0% | sí | no | sí | Zoho/A&S |
| Póliza - Fecha fin vigencia | `P_liza_Fecha_fin_de_la_vigencia` | date | 1 | 100.0% | sí | no | sí | Zoho/A&S |
| Renovable | `Renovable` | picklist | 1 | 100.0% | sí | no | sí | Zoho/A&S |
| Medio de pago | `Medio_de_pago` | picklist | 1 | 100.0% | sí | pendiente | sí | Cliente/A&S |
| Comisión | `Comisi_n1` | formula | 1 | 100.0% | sí | no | no | Sistema |
| IVA | `IVA1` | formula | 1 | 100.0% | sí | no | no | Sistema |
| Referencia (Plan) | `Referencia_Plan` | textarea | 1 | 100.0% | sí | pendiente | sí | Cliente/A&S |
| Vendedor | `Vendedor` | picklist | 1 | 100.0% | sí | no | sí | A&S |
| Cambio intermediario | `Cambio_de_intermediario` | picklist | 1 | 100.0% | sí | no | sí | Zoho/A&S |
| Reemplaza póliza actual | `Reemplaza_p_liza_actual` | picklist | 1 | 100.0% | sí | no | sí | Zoho/A&S |
| Cambio forma de pago | `Cambio_de_forma_de_pago` | boolean | 1 | 100.0% | sí | pendiente | sí | Cliente/A&S |
| Estado de la póliza | `Estado_de_la_p_liza` | picklist | 1 | 100.0% | sí | no | sí | Zoho/A&S |
| Correo comercial | `Correo_gesti_n_comercial` | email | 1 | 100.0% | enmascarado | no | sí | Zoho/A&S |
| Correo facturación | `Correo_facturaci_n` | email | 1 | 100.0% | enmascarado | no | sí | Zoho/A&S |
| Aseguradora | `Aseguradora1` | picklist | 1 | 100.0% | sí | pendiente | sí | Cliente/A&S |
| Key | `Key` | text | 1 | 100.0% | sí | no | sí | Zoho/A&S |
| Cobro mercancias | `Cobro_mercancias` | boolean | 1 | 100.0% | sí | no | sí | Zoho/A&S |
| Activar póliza | `Activar_p_liza` | boolean | 1 | 100.0% | sí | no | sí | Zoho/A&S |
| Recordar pago periódico | `Aviso_de_cobro` | boolean | 1 | 100.0% | sí | pendiente | sí | Cliente/A&S |
| ¿Agregar otro correo? | `Agregar_otro_correo` | boolean | 1 | 100.0% | enmascarado | no | sí | Zoho/A&S |
| Otro correo electrónico | `Otro_correo_electr_nico` | email | 1 | 100.0% | enmascarado | no | sí | Zoho/A&S |
| Póliza en moneda extranjera | `P_liza_en_moneda_extranjera` | boolean | 1 | 100.0% | sí | no | sí | Zoho/A&S |
| Póliza corta | `P_liza_recortada` | formula | 1 | 100.0% | sí | no | no | Sistema |
| Actualizaciones | `Actualizaciones` | boolean | 1 | 100.0% | sí | no | sí | Zoho/A&S |
| Record Status | `Record_Status__s` | picklist | 1 | 100.0% | sí | no | sí | Zoho/A&S |
| % Comisión vendedor | `Comisi_n_vendedor` | percent | 1 | 100.0% | sí | no | sí | A&S |
| ¿Tiene fondo de ahorro? | `Tiene_fondo_de_ahorro1` | boolean | 1 | 100.0% | sí | no | sí | Zoho/A&S |
| Línea de negocio | `L_nea_de_negocio` | formula | 1 | 100.0% | sí | no | no | Sistema |
| Comisión acumulada total | `Comisi_n_acumulada_total` | rollup_summary | 1 | 100.0% | sí | no | no | Sistema |
| Endoso | `Endoso` | boolean | 1 | 100.0% | sí | no | sí | Zoho/A&S |
| Dia facturación | `Dia_facturaci_n` | integer | 1 | 100.0% | sí | no | sí | Zoho/A&S |
| Tipo de facturación | `Tipo_de_facturaci_n` | picklist | 1 | 100.0% | sí | no | sí | Zoho/A&S |

## Asegurados (`Riesgos1`)

- Procesados: **146** (completo).
- Estados agregados: {'Activo': 94, 'Excluido': 52}.
- Con riesgo relacionado: 0.
- Contactos relacionados verificados: 146.

| Label | API name | Tipo | Poblado | Cobertura | Cliente ve | Cliente edita | A&S edita | Origen |
|---|---|---|---:|---:|---|---|---|---|
| Subriesgo | `Name` | text | 146 | 100.0% | enmascarado | no | sí | Zoho/A&S |
| Asegurado Propietario | `Owner` | ownerlookup | 146 | 100.0% | sí | no | sí | A&S |
| Correo electrónico | `Email` | email | 7 | 4.8% | enmascarado | no | sí | Zoho/A&S |
| Creado por | `Created_By` | ownerlookup | 146 | 100.0% | sí | no | no | Sistema |
| Modificado por | `Modified_By` | ownerlookup | 146 | 100.0% | sí | no | no | Sistema |
| Hora de creación | `Created_Time` | datetime | 146 | 100.0% | sí | no | no | Sistema |
| Hora de modificación | `Modified_Time` | datetime | 146 | 100.0% | sí | no | no | Sistema |
| Hora de la última actividad | `Last_Activity_Time` | datetime | 146 | 100.0% | sí | no | no | Sistema |
| Moneda | `Currency` | picklist | 146 | 100.0% | sí | no | sí | Zoho/A&S |
| Tasa de cambio | `Exchange_Rate` | double | 146 | 100.0% | sí | no | no | Sistema |
| ID de registro | `id` | bigint | 146 | 100.0% | sí | no | no | Sistema |
| Locked | `Locked__s` | boolean | 146 | 100.0% | sí | no | no | Sistema |
| Correo electrónico beneficiario | `Correo_electr_nico_beneficiario` | email | 2 | 1.4% | enmascarado | pendiente | sí | Cliente/A&S |
| Afiliado | `Contacto_facturaci_n_dividida_colectivas` | lookup | 146 | 100.0% | sí | pendiente | sí | Cliente/A&S |
| Pago EMPRESA (Sin IVA) | `Prima` | currency | 126 | 86.3% | sí | pendiente | sí | Cliente/A&S |
| Póliza | `P_liza` | lookup | 146 | 100.0% | sí | no | sí | Zoho/A&S |
| Asegurado | `Asegurado` | lookup | 146 | 100.0% | sí | pendiente | sí | Cliente/A&S |
| Beneficiario | `Beneficiario` | lookup | 3 | 2.1% | enmascarado | pendiente | sí | Cliente/A&S |
| Estado asegurado | `Estado` | picklist | 146 | 100.0% | sí | pendiente | sí | Cliente/A&S |
| Valor asegurado | `Valor_asegurado` | currency | 5 | 3.4% | sí | pendiente | sí | Cliente/A&S |
| Key asegurado | `key_riesgo_asignado` | text | 146 | 100.0% | sí | pendiente | sí | Cliente/A&S |
| Fecha retiro | `Fecha_salida_riesgo` | date | 4 | 2.7% | sí | pendiente | sí | Cliente/A&S |
| Fecha ingreso | `Fecha_ingreso_riesgo` | date | 146 | 100.0% | sí | pendiente | sí | Cliente/A&S |
| IVA Empresa | `IVA` | formula | 146 | 100.0% | sí | no | no | Sistema |
| Pago total (Con IVA) | `Pago_total` | formula | 146 | 100.0% | sí | no | no | Sistema |
| ¿Agregar otro correo? | `Agregar_otro_correo` | boolean | 146 | 100.0% | enmascarado | no | sí | Zoho/A&S |
| Observaciones | `Observaciones` | textarea | 1 | 0.7% | enmascarado | pendiente | sí | Cliente/A&S |
| Plan | `Plan` | text | 146 | 100.0% | sí | pendiente | sí | Cliente/A&S |
| Actualizar Key | `Actualizar_Key` | boolean | 146 | 100.0% | sí | no | sí | Zoho/A&S |
| Record Status | `Record_Status__s` | picklist | 146 | 100.0% | sí | no | sí | Zoho/A&S |
| Asistencia | `Asistencia` | boolean | 146 | 100.0% | sí | no | sí | Zoho/A&S |
| Pago total Afiliado (Según la forma de pago) | `Pago_total_Seg_n_la_forma_de_pago_Valor_asegura` | currency | 123 | 84.2% | sí | pendiente | sí | Cliente/A&S |
| Correo electrónico afiliado | `Correo_electr_nico_afiliado` | email | 146 | 100.0% | enmascarado | no | sí | Zoho/A&S |
| Parentesco | `Parentesco` | picklist | 146 | 100.0% | sí | pendiente | sí | Cliente/A&S |
| Pago ASEGURADO (Sin IVA) | `Pago_EMPLEADO_Sin_IVA` | currency | 146 | 100.0% | sí | pendiente | sí | Cliente/A&S |
| Aseguradora | `Aseguradora` | text | 146 | 100.0% | sí | no | no | Sistema |
| Tomador | `Tomador` | text | 146 | 100.0% | sí | no | no | Sistema |
| IVA Asegurado | `IVA_Empleado` | formula | 146 | 100.0% | sí | no | no | Sistema |
| Ramo | `Ramo` | picklist | 146 | 100.0% | sí | no | sí | Zoho/A&S |
| % IVA | `IVA1` | formula | 146 | 100.0% | sí | no | no | Sistema |
| Validación prima anterior | `Validaci_n_prima_anterior` | formula | 146 | 100.0% | sí | no | no | Sistema |
| Asegurado con Gestor Asignado | `Asegurado_con_Gestor_Asignado` | boolean | 146 | 100.0% | sí | pendiente | sí | Cliente/A&S |
| Endoso | `Endoso` | boolean | 146 | 100.0% | sí | no | sí | Zoho/A&S |
| Valor asegurado total | `Valor_asegurado_total` | formula | 5 | 3.4% | sí | no | no | Sistema |
| ¿Crear tarea de seguimiento? | `Crear_tarea_de_seguimiento` | picklist | 5 | 3.4% | sí | no | sí | Zoho/A&S |

## Riesgos

- Riesgos vinculados por `Riesgos1.Riesgo`: **0**.

| Label | API name | Tipo | Poblado | Cobertura | Cliente ve | Cliente edita | A&S edita | Origen |
|---|---|---|---:|---:|---|---|---|---|
| Pendiente | — | — | 0 | 0% | — | — | — | — |

## Pago fraccionado

Los candidatos se derivan de metadata y cobertura real. `Polizas.Modo_de_pago` y `Polizas.Frecuencia` son los conceptos principales a comprobar; importes por cuota y pagos de `Riesgos1` se mantienen separados. La editabilidad por cliente queda pendiente de decisión funcional de A&S.
