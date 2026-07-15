# Politicas de acceso

## Configuracion central

`PolicyConfiguration` es singleton y contiene zona horaria, horarios, sesiones, reautenticacion, inactividad, vencimiento/escalamiento de alertas y comportamiento fuera de horario. Su cache dura cinco minutos y todo cambio administrativo la invalida inmediatamente.

Cambiar una politica exige MFA activo, sesion segura, reautenticacion `policy_admin` y motivo. El evento almacena solo nombres de campos y valores administrativos seguros; genera alerta y aparece en la linea de tiempo.

## Evaluacion

`evaluate_access_policy(user, role, operation, at)` considera, en orden: excepcion especifica, usuario, rol, operacion, ventana, festivo, dia y horario. Devuelve una estructura explicable con `allowed`, `within_schedule`, `reason`, `applied_policy`, `severity`, `requires_reauthentication`, `requires_alert`, `requires_block`, `exception_applied` y `policy_identifier`.

Modalidades implementadas: permitir, permitir y alertar, exigir reautenticacion y bloquear. La arquitectura deja espacio para aprobacion futura, pero no la simula ni concede aprobaciones implicitas.

## Festivos Colombia

`load_colombia_holidays --year AAAA` calcula localmente festivos fijos, Ley Emiliani y fechas relativas a Pascua. No depende de Internet. El Administrador puede agregar una fecha interna o marcar un festivo laborable, siempre con reautenticacion, motivo, auditoria y alerta.

## Excepciones

Tipos: permitir, bloquear, ampliar horario y vacaciones/pausa operativa. Pueden ser globales, por usuario o rol y limitar operaciones/horas. `ends_at` debe ser posterior a `starts_at`; el comando programado expira las vencidas. Revocar exige motivo y no elimina la fila.

Las vacaciones activas suprimen la alerta general de adopcion para evitar afirmar incumplimiento. El texto autorizado es “Posible uso paralelo de Excel u otra herramienta no autorizada” o “El sistema no presenta actividad operativa reciente; validar adopcion del proceso”.

## Produccion

Configure `America/Bogota`, cargue el ano actual y siguiente, revise las excepciones, pruebe cada modalidad y programe `evaluate_security_policies`. No use datos reales hasta cerrar los riesgos descritos en la arquitectura de seguridad.
