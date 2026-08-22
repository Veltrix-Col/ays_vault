# Movilidad colectivo

## Identificación técnica

- Póliza autorizada: `900000288971`.
- Perfil: `production`; backend: `sdk`; solo lectura.
- Layout: configured; ramo: Movilidad colectivo; aseguradora: Seguros Generales Suramericana S.A.
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
| Cambio forma de pago | `Cambio_de_forma_de_pago` | boolean | 1 | 100.0% | sí | pendiente | sí | Cliente/A&S |
| Estado de la póliza | `Estado_de_la_p_liza` | picklist | 1 | 100.0% | sí | no | sí | Zoho/A&S |
| Correo facturación | `Correo_facturaci_n` | email | 1 | 100.0% | enmascarado | no | sí | Zoho/A&S |
| Aseguradora | `Aseguradora1` | picklist | 1 | 100.0% | sí | pendiente | sí | Cliente/A&S |
| Key | `Key` | text | 1 | 100.0% | sí | no | sí | Zoho/A&S |
| Cobro mercancias | `Cobro_mercancias` | boolean | 1 | 100.0% | sí | no | sí | Zoho/A&S |
| Activar póliza | `Activar_p_liza` | boolean | 1 | 100.0% | sí | no | sí | Zoho/A&S |
| Recordar pago periódico | `Aviso_de_cobro` | boolean | 1 | 100.0% | sí | pendiente | sí | Cliente/A&S |
| ¿Agregar otro correo? | `Agregar_otro_correo` | boolean | 1 | 100.0% | enmascarado | no | sí | Zoho/A&S |
| Otro correo electrónico | `Otro_correo_electr_nico` | email | 1 | 100.0% | enmascarado | no | sí | Zoho/A&S |
| Link de pago | `Link_de_pago` | website | 1 | 100.0% | enmascarado | pendiente | sí | Cliente/A&S |
| NIT Aseguradora | `NIT_Aseguradora` | text | 1 | 100.0% | enmascarado | pendiente | sí | Cliente/A&S |
| Póliza en moneda extranjera | `P_liza_en_moneda_extranjera` | boolean | 1 | 100.0% | sí | no | sí | Zoho/A&S |
| Póliza corta | `P_liza_recortada` | formula | 1 | 100.0% | sí | no | no | Sistema |
| Actualizaciones | `Actualizaciones` | boolean | 1 | 100.0% | sí | no | sí | Zoho/A&S |
| Record Status | `Record_Status__s` | picklist | 1 | 100.0% | sí | no | sí | Zoho/A&S |
| Linea de asistencia | `Linea_de_asistencia` | formula | 1 | 100.0% | sí | no | no | Sistema |
| % Comisión vendedor | `Comisi_n_vendedor` | percent | 1 | 100.0% | sí | no | sí | A&S |
| ¿Tiene fondo de ahorro? | `Tiene_fondo_de_ahorro1` | boolean | 1 | 100.0% | sí | no | sí | Zoho/A&S |
| Póliza larga | `P_liza_larga` | text | 1 | 100.0% | sí | no | sí | Zoho/A&S |
| Línea de negocio | `L_nea_de_negocio` | formula | 1 | 100.0% | sí | no | no | Sistema |
| Comisión acumulada total | `Comisi_n_acumulada_total` | rollup_summary | 1 | 100.0% | sí | no | no | Sistema |
| Endoso | `Endoso` | boolean | 1 | 100.0% | sí | no | sí | Zoho/A&S |
| Dia facturación | `Dia_facturaci_n` | integer | 1 | 100.0% | sí | no | sí | Zoho/A&S |
| Tipo de facturación | `Tipo_de_facturaci_n` | picklist | 1 | 100.0% | sí | no | sí | Zoho/A&S |

## Asegurados (`Riesgos1`)

- Procesados: **137** (completo).
- Estados agregados: {'Activo': 81, 'Activo con ajuste': 2, 'Excluido': 52, 'Excluido con cobro': 2}.
- Con riesgo relacionado: 137.
- Contactos relacionados verificados: 147.

| Label | API name | Tipo | Poblado | Cobertura | Cliente ve | Cliente edita | A&S edita | Origen |
|---|---|---|---:|---:|---|---|---|---|
| Subriesgo | `Name` | text | 137 | 100.0% | enmascarado | no | sí | Zoho/A&S |
| Asegurado Propietario | `Owner` | ownerlookup | 137 | 100.0% | sí | no | sí | A&S |
| Correo electrónico | `Email` | email | 72 | 52.6% | enmascarado | no | sí | Zoho/A&S |
| Creado por | `Created_By` | ownerlookup | 137 | 100.0% | sí | no | no | Sistema |
| Modificado por | `Modified_By` | ownerlookup | 137 | 100.0% | sí | no | no | Sistema |
| Hora de creación | `Created_Time` | datetime | 137 | 100.0% | sí | no | no | Sistema |
| Hora de modificación | `Modified_Time` | datetime | 137 | 100.0% | sí | no | no | Sistema |
| Hora de la última actividad | `Last_Activity_Time` | datetime | 137 | 100.0% | sí | no | no | Sistema |
| Moneda | `Currency` | picklist | 137 | 100.0% | sí | no | sí | Zoho/A&S |
| Tasa de cambio | `Exchange_Rate` | double | 137 | 100.0% | sí | no | no | Sistema |
| ID de registro | `id` | bigint | 137 | 100.0% | sí | no | no | Sistema |
| Locked | `Locked__s` | boolean | 137 | 100.0% | sí | no | no | Sistema |
| Correo electrónico beneficiario | `Correo_electr_nico_beneficiario` | email | 9 | 6.6% | enmascarado | pendiente | sí | Cliente/A&S |
| Afiliado | `Contacto_facturaci_n_dividida_colectivas` | lookup | 97 | 70.8% | sí | pendiente | sí | Cliente/A&S |
| Correo facturación dividida | `Correo_facturaci_n_dividida` | email | 32 | 23.4% | enmascarado | no | sí | Zoho/A&S |
| Pago EMPRESA (Sin IVA) | `Prima` | currency | 129 | 94.2% | sí | pendiente | sí | Cliente/A&S |
| Póliza | `P_liza` | lookup | 137 | 100.0% | sí | no | sí | Zoho/A&S |
| Asegurado | `Asegurado` | lookup | 137 | 100.0% | sí | pendiente | sí | Cliente/A&S |
| Beneficiario | `Beneficiario` | lookup | 9 | 6.6% | enmascarado | pendiente | sí | Cliente/A&S |
| Estado asegurado | `Estado` | picklist | 137 | 100.0% | sí | pendiente | sí | Cliente/A&S |
| Riesgo | `Riesgo` | lookup | 137 | 100.0% | sí | no | sí | Zoho/A&S |
| Valor asegurado | `Valor_asegurado` | currency | 132 | 96.4% | sí | pendiente | sí | Cliente/A&S |
| Key asegurado | `key_riesgo_asignado` | text | 137 | 100.0% | sí | pendiente | sí | Cliente/A&S |
| Fecha retiro | `Fecha_salida_riesgo` | date | 27 | 19.7% | sí | pendiente | sí | Cliente/A&S |
| Fecha ingreso | `Fecha_ingreso_riesgo` | date | 95 | 69.3% | sí | pendiente | sí | Cliente/A&S |
| IVA Empresa | `IVA` | formula | 137 | 100.0% | sí | no | no | Sistema |
| Pago total (Con IVA) | `Pago_total` | formula | 137 | 100.0% | sí | no | no | Sistema |
| ¿Agregar otro correo? | `Agregar_otro_correo` | boolean | 137 | 100.0% | enmascarado | no | sí | Zoho/A&S |
| Otro correo electrónico | `Otro_correo_electr_nico` | email | 1 | 0.7% | enmascarado | no | sí | Zoho/A&S |
| Observaciones | `Observaciones` | textarea | 16 | 11.7% | enmascarado | pendiente | sí | Cliente/A&S |
| Plan | `Plan` | text | 97 | 70.8% | sí | pendiente | sí | Cliente/A&S |
| Fecha renovación SOAT | `Fecha_de_vencimiento_SOAT` | date | 64 | 46.7% | sí | no | sí | Zoho/A&S |
| Actualizar Key | `Actualizar_Key` | boolean | 137 | 100.0% | sí | no | sí | Zoho/A&S |
| Email expedir SOAT | `Email_expedir_SOAT` | picklist | 6 | 4.4% | enmascarado | no | sí | Zoho/A&S |
| Gestión SOAT A&S | `Gesti_n_SOAT_A_S` | picklist | 19 | 13.9% | sí | no | sí | Zoho/A&S |
| Record Status | `Record_Status__s` | picklist | 137 | 100.0% | sí | no | sí | Zoho/A&S |
| Fecha asignación Gestión SOAT A&S | `Fecha_asignaci_n_Gesti_n_SOAT_A_S` | date | 43 | 31.4% | sí | no | sí | Zoho/A&S |
| Asistencia | `Asistencia` | boolean | 137 | 100.0% | sí | no | sí | Zoho/A&S |
| Pago total Afiliado (Según la forma de pago) | `Pago_total_Seg_n_la_forma_de_pago_Valor_asegura` | currency | 47 | 34.3% | sí | pendiente | sí | Cliente/A&S |
| Correo electrónico afiliado | `Correo_electr_nico_afiliado` | email | 86 | 62.8% | enmascarado | no | sí | Zoho/A&S |
| Parentesco | `Parentesco` | picklist | 13 | 9.5% | sí | pendiente | sí | Cliente/A&S |
| Pago ASEGURADO (Sin IVA) | `Pago_EMPLEADO_Sin_IVA` | currency | 95 | 69.3% | sí | pendiente | sí | Cliente/A&S |
| Aseguradora | `Aseguradora` | text | 137 | 100.0% | sí | no | no | Sistema |
| Tomador | `Tomador` | text | 137 | 100.0% | sí | no | no | Sistema |
| IVA Asegurado | `IVA_Empleado` | formula | 95 | 69.3% | sí | no | no | Sistema |
| Ramo | `Ramo` | picklist | 137 | 100.0% | sí | no | sí | Zoho/A&S |
| % IVA | `IVA1` | formula | 137 | 100.0% | sí | no | no | Sistema |
| Validación prima anterior | `Validaci_n_prima_anterior` | formula | 132 | 96.4% | sí | no | no | Sistema |
| Asegurado con Gestor Asignado | `Asegurado_con_Gestor_Asignado` | boolean | 137 | 100.0% | sí | pendiente | sí | Cliente/A&S |
| Responsable SOAT | `Responsable_SOAT` | picklist | 45 | 32.8% | sí | no | sí | Zoho/A&S |
| Endoso | `Endoso` | boolean | 137 | 100.0% | sí | no | sí | Zoho/A&S |
| Estado del endoso | `Estado_del_endoso` | picklist | 1 | 0.7% | sí | no | sí | Zoho/A&S |
| Beneficiario Oneroso | `Beneficiario_Oneroso` | picklist | 1 | 0.7% | enmascarado | pendiente | sí | Cliente/A&S |
| Valor asegurado total | `Valor_asegurado_total` | formula | 116 | 84.7% | sí | no | no | Sistema |
| ¿Crear tarea de seguimiento? | `Crear_tarea_de_seguimiento` | picklist | 13 | 9.5% | sí | no | sí | Zoho/A&S |
| Fecha de modificación | `Fecha_de_modificaci_n` | date | 2 | 1.5% | sí | no | sí | Zoho/A&S |

## Riesgos

- Riesgos vinculados por `Riesgos1.Riesgo`: **135**.

| Label | API name | Tipo | Poblado | Cobertura | Cliente ve | Cliente edita | A&S edita | Origen |
|---|---|---|---:|---:|---|---|---|---|
| Key Riesgo | `Name` | text | 135 | 100.0% | enmascarado | no | sí | Zoho/A&S |
| Riesgo Propietario | `Owner` | ownerlookup | 135 | 100.0% | sí | no | sí | A&S |
| Creado por | `Created_By` | ownerlookup | 135 | 100.0% | sí | no | no | Sistema |
| Modificado por | `Modified_By` | ownerlookup | 135 | 100.0% | sí | no | no | Sistema |
| Hora de creación | `Created_Time` | datetime | 135 | 100.0% | sí | no | no | Sistema |
| Hora de modificación | `Modified_Time` | datetime | 135 | 100.0% | sí | no | no | Sistema |
| Hora de la última actividad | `Last_Activity_Time` | datetime | 134 | 99.3% | sí | no | no | Sistema |
| Moneda | `Currency` | picklist | 135 | 100.0% | sí | no | sí | Zoho/A&S |
| Tasa de cambio | `Exchange_Rate` | double | 135 | 100.0% | sí | no | no | Sistema |
| Diseño | `Layout` | layout | 135 | 100.0% | sí | no | no | Sistema |
| ID de registro | `id` | bigint | 135 | 100.0% | sí | no | no | Sistema |
| Ciudad | `Ciudad` | picklist | 125 | 92.6% | sí | no | sí | Zoho/A&S |
| Locked | `Locked__s` | boolean | 135 | 100.0% | sí | no | no | Sistema |
| Placa | `Placa_del_vehiculo` | text | 135 | 100.0% | sí | no | sí | Zoho/A&S |
| Dirección | `Direccion` | text | 114 | 84.4% | enmascarado | no | sí | Zoho/A&S |
| Marca o referencia | `Marca_Tipo_Caracter_sticas` | text | 130 | 96.3% | sí | no | sí | Zoho/A&S |
| Código Fasecolda | `C_digo_FASECOLDA` | integer | 85 | 63.0% | sí | no | sí | Zoho/A&S |
| Clase | `Clase` | picklist | 118 | 87.4% | sí | no | sí | Zoho/A&S |
| Cilindraje C.C. o pasajeros | `Cilindraje_C_C_o_pasajeros` | picklist | 112 | 83.0% | sí | no | sí | Zoho/A&S |
| Key Riesgo Vehiculo | `Key_Riesgo_Vehiculo` | formula | 135 | 100.0% | sí | no | no | Sistema |
| Key riesgo inmueble | `Key_riesgo_inmueble` | formula | 114 | 84.4% | sí | no | no | Sistema |
| Modelo | `Modelo` | integer | 135 | 100.0% | sí | no | sí | Zoho/A&S |
| Edad | `Edad` | formula | 135 | 100.0% | sí | no | no | Sistema |
| Fecha renovación SOAT | `Fecha_de_renovaci_n_SOAT` | date | 121 | 89.6% | sí | no | sí | Zoho/A&S |
| ID de la mascota | `ID_Mascota` | text | 52 | 38.5% | sí | no | sí | Zoho/A&S |
| Campaña de renovación | `Enviar_correo_de_renovaci_n_Oportunidad` | picklist | 125 | 92.6% | enmascarado | no | sí | Zoho/A&S |
| Record Status | `Record_Status__s` | picklist | 135 | 100.0% | sí | no | sí | Zoho/A&S |
| Motor | `Motor` | text | 99 | 73.3% | sí | no | sí | Zoho/A&S |
| Chasis | `Chasis` | text | 99 | 73.3% | sí | no | sí | Zoho/A&S |
| Gestión SOAT | `Gesti_n_SOAT2` | picklist | 23 | 17.0% | sí | no | sí | Zoho/A&S |
| Observaciones de gestión | `Observaciones_de_gesti_n` | text | 17 | 12.6% | enmascarado | pendiente | sí | Cliente/A&S |
| Códig Fasecolda | `C_dig_Fasecolda` | integer | 126 | 93.3% | sí | no | sí | Zoho/A&S |
| Tipo de riesgo | `Tipo_de_riesgo` | picklist | 135 | 100.0% | sí | no | sí | Zoho/A&S |
| Verificado | `Verificado` | boolean | 135 | 100.0% | sí | no | sí | Zoho/A&S |
| Sin número de contrato | `Sin_n_mero_de_contrato` | boolean | 135 | 100.0% | sí | no | sí | Zoho/A&S |

## Pago fraccionado

Los candidatos se derivan de metadata y cobertura real. `Polizas.Modo_de_pago` y `Polizas.Frecuencia` son los conceptos principales a comprobar; importes por cuota y pagos de `Riesgos1` se mantienen separados. La editabilidad por cliente queda pendiente de decisión funcional de A&S.
