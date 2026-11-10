"""Helpers for EduPage behaviour notes (timeline type ``vcelicka``).

EduPage records praise and reprimands ("Včielka" / behaviour notes) as
timeline items without text. The note category is stored in the additional
data, for example::

    {"typ": "disrespecting", "typ_text": "Nerešpektuje pokyny učiteľa",
     "predmetid": "6", "vcelickaid": 1304438, "mesiac": "P1"}
"""

from __future__ import annotations

from typing import Any

from .assignment_helpers import _normalize_name, event_recipient

NOTE_TYPES = frozenset({"vcelicka"})


def _clean(value: Any) -> str | None:
    """Return a stripped string, or None for empty / null-like values."""
    if isinstance(value, str):
        value = value.strip()
        if value and value.lower() not in ("null", "none"):
            return value
    return None


def note_attributes(event: Any) -> dict[str, Any]:
    """Return structured attributes for a behaviour-note timeline event."""
    data = getattr(event, "additional_data", None) or {}
    if not isinstance(data, dict):
        data = {}
    label = _clean(data.get("typ_text"))
    return {
        "text": _clean(getattr(event, "text", None)) or label,
        "note_type": _clean(data.get("typ")),
        "note_label": label,
    }


def note_matches_student(event: Any, student_id: Any, student_name: Any) -> bool:
    """Return whether a behaviour note can be attributed to this student.

    Unlike homework, a behaviour note is personal. Parent accounts share one
    timeline across siblings, so a note is only attributed when the recipient
    identifies this student: either by a person ID or by an exact (accent- and
    case-insensitive) name match. Missing, school-wide, class, group, or
    otherwise ambiguous recipients are never attributed.
    """
    recipient = getattr(event, "recipient", None)
    if recipient is None:
        return False

    recipient_id = getattr(recipient, "person_id", None)
    if recipient_id is not None:
        return student_id is not None and str(recipient_id) == str(student_id)

    recipient_name = event_recipient(event)
    if not recipient_name or not student_name:
        return False
    normalized = _normalize_name(recipient_name)
    if not normalized or normalized == "*":
        return False
    return normalized == _normalize_name(student_name)
