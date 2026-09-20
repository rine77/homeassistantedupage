"""Helpers for linking EduPage timeline events to grade objects."""

from __future__ import annotations

from typing import Any


def _reference(value: Any) -> str | None:
    """Return a comparable scalar reference."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (str, int, float)):
        return str(value).strip()
    return None


def grade_reference_ids(event: Any) -> set[str]:
    """Return grade event IDs embedded in timeline additional data.

    EduPage's ``TimelineEvent.event_id`` is a timeline ID, while an
    ``EduGrade.event_id`` is the underlying school-event ID. Grade timeline
    payloads carry the latter in nested ``udalostid`` fields below a dynamic
    top-level key.
    """
    references: set[str] = set()
    budget = [500]

    def inspect(value: Any, depth: int = 0) -> None:
        if depth > 6 or budget[0] <= 0:
            return
        budget[0] -= 1
        if isinstance(value, dict):
            for key, item in value.items():
                if str(key) == "udalostid" and (
                    reference := _reference(item)
                ):
                    references.add(reference)
                inspect(item, depth + 1)
        elif isinstance(value, (list, tuple)):
            for item in value:
                inspect(item, depth + 1)

    inspect(getattr(event, "additional_data", None) or {})
    return references


def matching_grade(event: Any, grades: list[Any]) -> Any | None:
    """Return the grade referenced by a timeline event, when available."""
    embedded_references = grade_reference_ids(event)
    timeline_reference = _reference(getattr(event, "event_id", None))

    # The embedded school-event ID is authoritative. Keep the historical
    # direct timeline-ID comparison as a fallback for other EduPage variants.
    references = list(embedded_references)
    if timeline_reference is not None:
        references.append(timeline_reference)

    for reference in references:
        for grade in grades:
            if _reference(getattr(grade, "event_id", None)) == reference:
                return grade
    return None
