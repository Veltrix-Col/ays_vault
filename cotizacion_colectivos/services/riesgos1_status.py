"""Shared operability rules for persisted Riesgos1 members."""

from __future__ import annotations

from datetime import date, datetime
import unicodedata


# Confirmed local contract values. Unknown states fail closed.
OPERABLE_RIESGOS1_STATES = frozenset({"activo", "activo con ajuste", "activo sin cobro"})


def _fold(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").strip().casefold())
    return "".join(char for char in text if not unicodedata.combining(char))


def _as_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def is_operable_riesgos1(*, state: object, exit_date: object = None, reference_date: date | None = None) -> bool:
    """Return whether a Riesgos1 row may be exposed as an operable member."""
    if _fold(state) not in OPERABLE_RIESGOS1_STATES:
        return False
    effective_exit = _as_date(exit_date)
    return effective_exit is None or effective_exit > (reference_date or date.today())


def filter_operable_riesgos1_members(members, *, reference_date: date | None = None):
    return tuple(
        member for member in (members or ())
        if is_operable_riesgos1(
            state=getattr(member, "state", ""),
            exit_date=getattr(member, "exit_date", ""),
            reference_date=reference_date,
        )
    )
