from django import template

from email_exceptions.services import normalize_email_body


register = template.Library()


@register.filter
def email_body_text(value):
    return normalize_email_body(value)
