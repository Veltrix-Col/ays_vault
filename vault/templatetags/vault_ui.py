from django import template

register = template.Library()

ALERT_TYPES = {
    "OUTSIDE_HOURS": "Acceso fuera de horario", "NEW_DEVICE": "Dispositivo nuevo",
    "MFA_BLOCKED": "MFA bloqueado", "USER_BLOCKED": "Usuario bloqueado",
    "PASSWORD_CHANGE": "Cambio de contraseña", "MFA_RESET": "Reinicio de MFA",
    "POLICY_CHANGED": "Política modificada", "EXCEPTION_CREATED": "Excepción creada",
    "EXCEPTION_REVOKED": "Excepción revocada", "SYSTEM_INACTIVITY": "Sistema sin uso",
    "POSSIBLE_PARALLEL_TOOL_USE": "Posible uso paralelo de Excel u otra herramienta no autorizada",
    "AUDIT_INTEGRITY_REVIEW": "Revisión de integridad de auditoría", "CRITICAL_ALERT": "Alerta crítica",
    "EMAIL_FAILURE": "Fallo de correo", "INACTIVE_USER": "Usuario inactivo",
    "HOLIDAY_UPCOMING": "Festivo próximo", "COPY": "Copia de información",
    "REVEAL": "Revelado de información", "TEST": "Alerta de prueba",
}
RESULTS = {"SUCCESS": "Exitoso", "FAILED": "Fallido", "DENIED": "Denegado", "BLOCKED": "Bloqueado", "PENDING": "Pendiente"}
ROLES = {"ADMIN": "Administrador", "LEADER": "Líder de cartera", "ANALYST": "Analista"}
HEALTH = {"HEALTHY": "Saludable", "ATTENTION": "Atención", "RISK": "Riesgo", "CRITICAL": "Crítico"}
ACTIONS = {"REVIEW": "Revisar", "JUSTIFY": "Justificar", "ESCALATE": "Escalar", "CLOSE": "Cerrar", "REOPEN": "Reabrir", "ASSIGN": "Asignar", "TRANSITION": "Cambio de estado"}
STATUSES = {"NEW": "Nueva", "IN_REVIEW": "En revisión", "JUSTIFIED": "Justificada", "ESCALATED": "Escalada", "CLOSED": "Cerrada", "REOPENED": "Reabierta"}
METADATA_LABELS = {
    "days_without_use": "Días sin uso", "last_login": "Último ingreso", "last_copy": "Última copia",
    "last_reveal": "Último revelado", "last_card_created": "Última tarjeta creada",
    "active_users": "Usuarios activos", "system_status": "Estado del sistema", "operation": "Operación",
    "policy": "Política", "exception": "Excepción", "reason": "Motivo",
}

@register.filter
def alert_type_label(value):
    return ALERT_TYPES.get(value, str(value or "Alerta de seguridad").replace("_", " ").capitalize())

@register.filter
def result_label(value):
    return RESULTS.get(value, str(value or "—").replace("_", " ").capitalize())

@register.filter
def role_label(value):
    return ROLES.get(value, value or "Sistema")

@register.filter
def health_label(value):
    return HEALTH.get(value, value or "Sin información")

@register.filter
def action_label(value):
    return ACTIONS.get(value, str(value or "—").replace("_", " ").capitalize())

@register.filter
def status_label(value):
    return STATUSES.get(value, str(value or "—").replace("_", " ").capitalize())

@register.filter
def safe_metadata_items(value):
    if not isinstance(value, dict):
        return []
    return [(METADATA_LABELS[key], item) for key, item in value.items() if key in METADATA_LABELS]
