"""Helpers shared by EduPage assignment entities."""

from __future__ import annotations

from collections.abc import Iterable
import re
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


def _contains_class_name(recipient: str, class_name: str) -> bool:
    """Return whether a recipient contains a delimited class name."""
    return bool(
        re.search(
            rf"(?<!\w){re.escape(class_name)}(?!\w)",
            recipient,
        )
    )


def event_matches_student(
    event: Any,
    student_id: Any,
    student_name: str | None,
    student_class_names: Iterable[str] = (),
) -> bool:
    """Return whether an assignment event belongs to the selected student.

    EduPage 0.12.5 normally exposes the raw ``user_meno`` value as a string in
    ``TimelineEvent.recipient``. Support an account object as well so a future
    API version can provide a stable person ID without another integration
    change. Class and teaching-group recipients are matched through the
    selected student's class name. Missing and school-wide recipients remain
    visible because they cannot be assigned safely to one student.
    """
    recipient = getattr(event, "recipient", None)
    if recipient is None:
        return True

    recipient_id = getattr(recipient, "person_id", None)
    if recipient_id is not None and student_id is not None:
        return str(recipient_id) == str(student_id)

    recipient_name = event_recipient(event)
    if recipient_name in (None, "", "*"):
        return True

    normalized_recipient = _normalize_name(recipient_name)
    if normalized_recipient in {
        "cela skola",
        "entire school",
        "gesamte schule",
        "whole school",
    }:
        return True

    if student_name and normalized_recipient == _normalize_name(student_name):
        return True

    normalized_classes = [
        _normalize_name(class_name)
        for class_name in student_class_names
        if class_name
    ]
    for normalized_class in normalized_classes:
        if normalized_class and _contains_class_name(
            normalized_recipient, normalized_class
        ):
            return True

    # If class metadata could not be loaded, keep the event rather than hiding
    # a potentially valid assignment. The list may remain mixed for that poll,
    # but no homework disappears because an optional lookup failed.
    return not normalized_classes and bool(
        " · " in normalized_recipient
        or " - " in normalized_recipient
        or re.match(r"^\d{1,2}\s*[a-z]?(?:\b|$)", normalized_recipient)
    )
