"""Authorization helpers for the human-facing email-exceptions module."""

VIEW_PERMISSION = "email_exceptions.view_email_exceptions_operational"
OPERATE_PERMISSION = "email_exceptions.operate_email_exceptions"


def _active_user(user):
    return bool(user and user.is_authenticated and user.is_active)


def can_view(user) -> bool:
    """OPERATOR implies VIEWER; delegated SSO access alone grants neither."""
    return _active_user(user) and (
        user.has_perm(VIEW_PERMISSION) or user.has_perm(OPERATE_PERMISSION)
    )


def can_operate(user) -> bool:
    return _active_user(user) and user.has_perm(OPERATE_PERMISSION)
