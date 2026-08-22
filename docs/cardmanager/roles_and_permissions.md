# Roles y permisos de CardManager

| Capacidad | Administrador | Líder de cartera | Analista |
|---|---:|---:|---:|
| Centro de control, políticas y alertas | Sí | No | No |
| Reportes administrativos seguros | Sí | Según vista: no por defecto | No |
| Crear/editar/desactivar tarjetas | No por herencia | Sí | No |
| Ver tarjetas | No por herencia | Sí | Sí, solo activas |
| Revelar/copiar datos protegidos | No por herencia | Sí | Sí |
| Gestionar sesiones/dispositivos/MFA ajenos | Sí | No | No |

La separación se implementa en `UserProfile`, `role_required`, filtros de QuerySet y pruebas de IDOR. La barra lateral se adapta al rol, pero la autorización real está en servidor. Usuarios inactivos o sin perfil válido no deben operar.

Las excepciones de horario pueden asignarse a usuario o rol, dentro de fechas y operaciones definidas. No elevan el rol funcional. La matriz debe ser confirmada por A&S antes de producción, especialmente la decisión de que Administrador no vea tarjetas por defecto.
