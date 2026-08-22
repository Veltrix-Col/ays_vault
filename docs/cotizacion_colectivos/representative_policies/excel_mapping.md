# Cruce con `Novedades Junio_Fonconstruimos.xlsx`

| Columna actual | Fuente candidata | Clasificación |
|---|---|---|
| Id asociado | `Contacts.Tipo_ID` + `Contacts.N_mero_de_ID` | Precargada, siempre enmascarada en UI |
| Nombre asociado | `Contacts.Full_Name` | Precargada |
| Id Asegurado | `Riesgos1.Asegurado → Contacts.N_mero_de_ID` | Precargada, enmascarada |
| Nombre Asegurado | `Riesgos1.Asegurado → Contacts.Full_Name` | Precargada |
| Póliza | `Polizas.Name` | Precargada, solo lectura |
| Ramo | `Polizas.Ramo` / `Riesgos1.Ramo` | Precargada; validar coherencia |
| Aseguradora | `Polizas.Aseguradora1` / `Riesgos1.Aseguradora` | Precargada |
| Pago Mensual (Con IVA) Asegurado | campos de pago `Riesgos1` | Pendiente escoger campo por ramo y validar IVA |
| Descuento Mensual Empleado | `Riesgos1.Pago_EMPLEADO_Sin_IVA` candidato | Pendiente: el Excel pide descuento y el campo Zoho declara sin IVA |
| Observaciones | `Riesgos1.Observaciones` / solicitud futura | Editable por cliente, revisión A&S pendiente |

Campos que debe añadir la plantilla futura: Tipo ID asociado, Tipo ID asegurado, Fecha efectiva, Tipo de novedad, Motivo, Estado de revisión, Valor anterior y Valor nuevo.

## Flujo propuesto

1. Excel actual: snapshot precargado desde Zoho.
2. Plantilla de novedades: columnas editables separadas de los valores vigentes.
3. Excel respondido: entrada del cliente sin sobrescribir el snapshot.
4. Comparativo: valor anterior/nuevo y validaciones.
5. Consolidado aprobado: decisión y campos internos de A&S.
