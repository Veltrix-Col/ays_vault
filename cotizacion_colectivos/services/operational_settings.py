from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone

from ..models import ColectivosOperationalSetting

logger = logging.getLogger("cotizacion_colectivos")
MONTHLY_RENEWALS_KEY = "monthly_renewals_enabled"


def monthly_renewals_enabled() -> bool:
    try:
        return bool(ColectivosOperationalSetting.objects.get(key=MONTHLY_RENEWALS_KEY).enabled)
    except Exception:
        logger.warning("colectivos_monthly_switch_read_failed", exc_info=True)
        return False


@transaction.atomic
def set_monthly_renewals_enabled(*, enabled: bool, actor=None) -> ColectivosOperationalSetting:
    setting, _ = ColectivosOperationalSetting.objects.select_for_update().get_or_create(
        key=MONTHLY_RENEWALS_KEY, defaults={"enabled": False}
    )
    setting.enabled = bool(enabled)
    setting.updated_by = actor
    setting.save(update_fields=("enabled", "updated_by", "updated_at"))
    return setting
