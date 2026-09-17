"""Authorization helpers for the human-facing email-exceptions module."""

VIEW_PERMISSION = "email_exceptions.view_email_exceptions_operational"
OPERATE_PERMISSION = "email_exceptions.operate_email_exceptions"


def _active_user(user):
    return bool(user and user.is_authenticated and user.is_active)


def can_view(user) -> bool:
    """Allow a validated inherited-access request or legacy local permission.

    ``_intranet_access_granted`` is attached by the trusted-intranet gate;
    ``_local_inherited_access_granted`` is attached by the explicitly
    development-only local gate.  The permission fallback keeps existing
    local users and direct service calls compatible while SSO users do not
    need a second, manually managed Django permission.
    """
    return bool(
        getattr(user, "_intranet_access_granted", False)
        or getattr(user, "_local_inherited_access_granted", False)
    ) or (
        _active_user(user)
        and (user.has_perm(VIEW_PERMISSION) or user.has_perm(OPERATE_PERMISSION))
    )


def can_operate(user) -> bool:
    return _active_user(user) and (
        bool(getattr(user, "_intranet_access_granted", False))
        or user.has_perm(OPERATE_PERMISSION)
    )
