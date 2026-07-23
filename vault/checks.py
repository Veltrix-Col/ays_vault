from django.core.checks import Error, register

from .email_config import email_configuration_issues


@register()
def check_email_configuration(app_configs, **kwargs):
    return [
        Error(issue.message, id=issue.code)
        for issue in email_configuration_issues()
    ]
