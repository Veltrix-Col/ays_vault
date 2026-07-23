from dataclasses import dataclass

from django.conf import settings


SUPPORTED_ALERT_BACKENDS = {"console", "smtp", "graph", "microsoft_graph"}
SUPPORTED_DJANGO_BACKENDS = {
    "django.core.mail.backends.console.EmailBackend",
    "django.core.mail.backends.locmem.EmailBackend",
    "django.core.mail.backends.smtp.EmailBackend",
}


@dataclass(frozen=True)
class EmailConfigurationIssue:
    code: str
    message: str


def normalized_backend(value=None):
    backend = (value if value is not None else settings.ALERT_EMAIL_BACKEND).strip().lower()
    return "graph" if backend == "microsoft_graph" else backend


def email_configuration_issues(settings_object=None):
    configured = settings_object or settings
    issues = [
        EmailConfigurationIssue("vault.EEMAIL001", message)
        for message in getattr(configured, "EMAIL_CONFIGURATION_ERRORS", [])
    ]
    backend = normalized_backend(getattr(configured, "ALERT_EMAIL_BACKEND", ""))
    django_backend = getattr(configured, "EMAIL_BACKEND", "")
    app_env = str(getattr(configured, "APP_ENV", "production")).strip().lower()
    development_environment = app_env in {"development", "dev", "test", "testing"}

    if backend not in {"console", "smtp", "graph"}:
        issues.append(EmailConfigurationIssue("vault.EEMAIL002", "ALERT_EMAIL_BACKEND no es reconocido; use console, smtp o graph."))
        return issues
    if django_backend not in SUPPORTED_DJANGO_BACKENDS:
        issues.append(EmailConfigurationIssue("vault.EEMAIL003", "EMAIL_BACKEND no es un backend de correo permitido."))

    sender = getattr(configured, "ALERT_EMAIL_FROM", "") or getattr(configured, "DEFAULT_FROM_EMAIL", "")
    if not sender:
        issues.append(EmailConfigurationIssue("vault.EEMAIL004", "El remitente de correo esta vacio."))

    timeout = getattr(configured, "EMAIL_TIMEOUT_SECONDS", 0)
    if not isinstance(timeout, int) or not 1 <= timeout <= 120:
        issues.append(EmailConfigurationIssue("vault.EEMAIL005", "EMAIL_TIMEOUT_SECONDS debe estar entre 1 y 120."))

    if backend == "console":
        if not development_environment:
            issues.append(EmailConfigurationIssue("vault.EEMAIL006", "El backend de consola no esta permitido fuera de desarrollo."))
        if django_backend not in {
            "django.core.mail.backends.console.EmailBackend",
            "django.core.mail.backends.locmem.EmailBackend",
        }:
            issues.append(EmailConfigurationIssue("vault.EEMAIL007", "La modalidad console requiere el backend console o locmem de Django."))

    if backend == "smtp":
        required = {
            "EMAIL_HOST": getattr(configured, "EMAIL_HOST", ""),
            "EMAIL_HOST_USER": getattr(configured, "EMAIL_HOST_USER", ""),
            "EMAIL_HOST_PASSWORD": getattr(configured, "EMAIL_HOST_PASSWORD", ""),
        }
        for name, value in required.items():
            if not value:
                issues.append(EmailConfigurationIssue("vault.EEMAIL008", f"{name} es requerido cuando SMTP esta activo."))
        port = getattr(configured, "EMAIL_PORT", 0)
        if not isinstance(port, int) or not 1 <= port <= 65535:
            issues.append(EmailConfigurationIssue("vault.EEMAIL009", "EMAIL_PORT debe ser un puerto valido."))
        tls = bool(getattr(configured, "EMAIL_USE_TLS", False))
        ssl = bool(getattr(configured, "EMAIL_USE_SSL", False))
        if tls and ssl:
            issues.append(EmailConfigurationIssue("vault.EEMAIL010", "TLS y SSL directo no pueden estar activos simultaneamente."))
        elif not tls or ssl:
            issues.append(EmailConfigurationIssue("vault.EEMAIL011", "SMTP de Microsoft 365 requiere TLS activo y SSL directo inactivo."))
        if django_backend != "django.core.mail.backends.smtp.EmailBackend":
            issues.append(EmailConfigurationIssue("vault.EEMAIL012", "La modalidad smtp requiere EMAIL_BACKEND de SMTP de Django."))

    if backend == "graph":
        required = (
            getattr(configured, "MS_GRAPH_TENANT_ID", ""),
            getattr(configured, "MS_GRAPH_CLIENT_ID", ""),
            getattr(configured, "MS_GRAPH_CLIENT_SECRET", ""),
            getattr(configured, "MS_GRAPH_SENDER", ""),
        )
        if not all(required):
            issues.append(EmailConfigurationIssue("vault.EEMAIL013", "La configuracion de Microsoft Graph esta incompleta."))
    return issues


def email_configuration_status():
    backend = normalized_backend()
    labels = {"console": "Consola", "smtp": "SMTP Microsoft 365", "graph": "Microsoft Graph"}
    return {
        "backend": backend,
        "backend_label": labels.get(backend, "No reconocido"),
        "sender": settings.ALERT_EMAIL_FROM or settings.DEFAULT_FROM_EMAIL,
        "configured": not email_configuration_issues(),
    }
