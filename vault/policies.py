from dataclasses import asdict, dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.core.cache import cache
from django.db import OperationalError, ProgrammingError
from django.utils import timezone

from .models import AccessException, Holiday, PolicyConfiguration


POLICY_CACHE_KEY = "vault:policy:v1"


@dataclass(frozen=True)
class AccessPolicyDecision:
    allowed: bool
    within_schedule: bool
    reason: str
    applied_policy: str
    severity: str
    requires_reauthentication: bool
    requires_alert: bool
    requires_block: bool
    exception_applied: int | None
    policy_identifier: int | None

    def as_dict(self):
        return asdict(self)


def invalidate_policy_cache():
    cache.delete(POLICY_CACHE_KEY)


def get_policy():
    policy = cache.get(POLICY_CACHE_KEY)
    if policy:
        return policy
    try:
        policy, _ = PolicyConfiguration.objects.get_or_create(singleton=1)
    except (OperationalError, ProgrammingError):
        policy = PolicyConfiguration(singleton=1)
    cache.set(POLICY_CACHE_KEY, policy, 300)
    return policy


def _localize(value, policy):
    try:
        zone = ZoneInfo(policy.timezone_name)
    except ZoneInfoNotFoundError:
        zone = ZoneInfo("America/Bogota")
    value = value or timezone.now()
    if timezone.is_naive(value):
        value = timezone.make_aware(value, zone)
    return value.astimezone(zone)


def _time_value(value):
    return time.fromisoformat(value) if isinstance(value, str) else value


def _matching_exception(user, role, operation, local_now):
    candidates = AccessException.objects.filter(
        status=AccessException.ACTIVE,
        starts_at__lte=local_now,
        ends_at__gte=local_now,
    ).order_by("-user_id", "-created_at")
    for exception in candidates:
        if exception.user_id and (not user or exception.user_id != user.pk):
            continue
        if exception.role and exception.role != role:
            continue
        if exception.operations and operation not in exception.operations:
            continue
        current = local_now.timetz().replace(tzinfo=None)
        if exception.daily_start and current < exception.daily_start:
            continue
        if exception.daily_end and current > exception.daily_end:
            continue
        return exception
    return None


def evaluate_access_policy(user=None, role="", operation="ACCESS", at=None):
    policy = get_policy()
    local_now = _localize(at, policy)
    exception = _matching_exception(user, role or getattr(getattr(user, "vault_profile", None), "role", ""), operation, local_now)
    if exception:
        blocked = exception.exception_type == AccessException.BLOCK
        return AccessPolicyDecision(
            allowed=not blocked,
            within_schedule=not blocked,
            reason=exception.reason,
            applied_policy=f"EXCEPTION_{exception.exception_type}",
            severity="CRITICAL" if blocked else "LOW",
            requires_reauthentication=False,
            requires_alert=True,
            requires_block=blocked,
            exception_applied=exception.pk,
            policy_identifier=policy.pk,
        )

    holiday = Holiday.objects.filter(date=local_now.date(), working_day=False).first()
    current = local_now.timetz().replace(tzinfo=None)
    weekday = local_now.weekday()
    if holiday:
        inside = False
        schedule_reason = f"Festivo: {holiday.name}"
    elif weekday < 5:
        inside = _time_value(policy.weekday_start) <= current <= _time_value(policy.weekday_end)
        schedule_reason = "Horario laboral" if inside else "Fuera del horario laboral"
    elif weekday == 5:
        inside = policy.saturday_enabled and _time_value(policy.saturday_start) <= current <= _time_value(policy.saturday_end)
        schedule_reason = "Horario de sabado" if inside else "Sabado no habilitado"
    else:
        inside = policy.sunday_enabled
        schedule_reason = "Domingo habilitado" if inside else "Domingo no habilitado"

    if inside:
        return AccessPolicyDecision(True, True, schedule_reason, "SCHEDULE", "LOW", False, False, False, None, policy.pk)
    behavior = policy.outside_hours_behavior
    blocked = behavior == "BLOCK"
    reauth = behavior == "REAUTH"
    alert = behavior in {"ALLOW_ALERT", "REAUTH", "BLOCK"}
    return AccessPolicyDecision(
        allowed=not blocked,
        within_schedule=False,
        reason=schedule_reason,
        applied_policy=behavior,
        severity="CRITICAL" if blocked else "HIGH",
        requires_reauthentication=reauth,
        requires_alert=alert,
        requires_block=blocked,
        exception_applied=None,
        policy_identifier=policy.pk,
    )


def is_within_allowed_schedule(user=None, role="", operation="ACCESS", at=None):
    return evaluate_access_policy(user=user, role=role, operation=operation, at=at).within_schedule
