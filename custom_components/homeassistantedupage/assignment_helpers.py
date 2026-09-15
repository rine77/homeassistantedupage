"""Helpers shared by EduPage assignment entities."""

from __future__ import annotations

from typing import Any

from unidecode import unidecode


def event_recipient(event: Any) -> Any:
    """Return a serializable recipient name when one is available."""
    recipient = getattr(event, "recipient", None)
    if recipient is None or isinstance(recipient, str):
        return recipient
    return getattr(recipient, "name", None) or str(recipient)


def _normalize_name(value: Any) -> str:
    """Normalize a person's display name for comparison."""
    return " ".join(unidecode(str(value)).casefold().split())


def event_matches_student(
    event: Any, student_id: Any, student_name: str | None
) -> bool:
    """Return whether an assignment event belongs to the selected student.

    EduPage 0.12.5 normally exposes the raw ``user_meno`` value as a string in
    ``TimelineEvent.recipient``. Support an account object as well so a future
    API version can provide a stable person ID without another integration
    change. Missing and wildcard recipients remain visible for backwards
    compatibility because they cannot be assigned safely to one student.
    """
    recipient = getattr(event, "recipient", None)
    if recipient is None:
        return True

    recipient_id = getattr(recipient, "person_id", None)
    if recipient_id is not None and student_id is not None:
        return str(recipient_id) == str(student_id)

    recipient_name = event_recipient(event)
    if recipient_name in (None, "", "*") or not student_name:
        return True

    return _normalize_name(recipient_name) == _normalize_name(student_name)
