"""Helpers for EduPage behaviour notes (timeline type ``vcelicka``).

EduPage records praise and reprimands ("Včielka" / behaviour notes) as
timeline items without text. The note category is stored in the additional
data, for example::

    {"typ": "disrespecting", "typ_text": "Nerešpektuje pokyny učiteľa",
     "predmetid": "6", "vcelickaid": 1304438, "mesiac": "P1"}
"""

from __future__ import annotations

from typing import Any

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
