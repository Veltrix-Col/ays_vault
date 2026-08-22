# Centro de Control

> Documento histórico conservado como antecedente. La referencia vigente es [`cardmanager/operations.md`](cardmanager/operations.md) y [`cardmanager/audit_and_monitoring.md`](cardmanager/audit_and_monitoring.md).

## Proposito

Vista exclusiva del Administrador para una operacion pequena. No mide concurrencia masiva, no crea rankings y no muestra PAN, vencimiento, valores revelados, secretos, tokens o codigos MFA.

## Contenido

- Salud explicable: `Saludable`, `Atencion`, `Riesgo` o `Critico`, siempre con causas.
- Estado de auditoria, MFA, base de datos, politicas, correo y ultima tarea programada.
- Ultimo acceso, copia, revelado, creacion, alerta, verificacion de cadena y resultado de correo.
- Alertas abiertas por severidad y vencimiento.
- Adopcion por usuario: ultimo ingreso, dias, copia, revelado, tarjetas consultadas y dispositivos reconocidos.
- Uso agregado 7/30/90 dias, dias sin uso, tarjetas nunca consultadas y consultadas recientemente.
- Proximos festivos y excepciones por vencer.

## Linea de tiempo

La Línea de Tiempo es exclusiva del Administrador. Incluye eventos técnicos, de identidad y operación sin Empresa, PAN, vencimiento ni valores revelados. Líder y Analista no pueden abrir la vista, parciales ni exportaciones, aunque sus acciones continúan auditándose internamente. Los filtros por usuario, rol, fecha, acción, riesgo, resultado, IP, dispositivo, horario, tarjeta y alerta se aplican en backend.

La pagina usa por defecto 50 eventos y permite 25, 50 o 100, orden ascendente o descendente y vista compacta/detallada. Los accesos rapidos cubren periodos, accesos, revelados, copias, alertas, fuera de horario y fallidos. Los filtros principales se muestran en una cuadricula adaptable; los criterios tecnicos permanecen en `Mas filtros`. El resumen presenta filtros activos como chips removibles.

Excel y PDF se generan exclusivamente por POST y requieren Administrador activo. El inventario administrativo de tarjetas es deliberadamente seguro: excluye Empresa, PAN y vencimiento y no descifra columnas protegidas. Consulte `REPORTING_AND_EXPORTS.md`.

## Operacion

1. Revisar primero salud y alertas criticas/vencidas.
2. Abrir la linea de tiempo y documentar el hallazgo.
3. Asignar, justificar, escalar o cerrar mediante reautenticacion.
4. Ejecutar `verify_audit_chain` y `evaluate_security_policies --dry-run` ante dudas.

## Riesgos pendientes

Se requieren KMS, SIEM inmutable, pruebas PostgreSQL/concurrencia, pentest, QA visual, backups cifrados verificados y configuracion productiva Microsoft 365. **No usar datos reales todavia.**
