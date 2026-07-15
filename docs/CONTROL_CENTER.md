# Centro de Control

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

Administrador ve eventos tecnicos, identidad y operacion sin valores de tarjeta. Lider ve eventos operativos. Analista ve solo eventos cuyo actor es el mismo usuario. Los filtros por usuario, rol, fecha, accion, riesgo, resultado, IP, dispositivo, horario, tarjeta y alerta se aplican al queryset ya limitado por rol para impedir IDOR.

La pagina contiene 50 eventos en orden descendente. Las vistas compacta y detallada solo cambian presentacion. No existe exportacion sensible; la separacion de filtros/queryset deja preparado un exportador futuro sujeto a autorizacion adicional.

## Operacion

1. Revisar primero salud y alertas criticas/vencidas.
2. Abrir la linea de tiempo y documentar el hallazgo.
3. Asignar, justificar, escalar o cerrar mediante reautenticacion.
4. Ejecutar `verify_audit_chain` y `evaluate_security_policies --dry-run` ante dudas.

## Riesgos pendientes

Se requieren KMS, SIEM inmutable, pruebas PostgreSQL/concurrencia, pentest, QA visual, backups cifrados verificados y configuracion productiva Microsoft 365. **No usar datos reales todavia.**
