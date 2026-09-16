"""Tests for student-specific assignment filtering."""

from types import SimpleNamespace

from custom_components.homeassistantedupage.assignment_helpers import (
    event_matches_student,
    event_recipient,
)


def test_matches_recipient_name_case_and_accent_insensitively():
    """EduPage recipient strings are matched robustly to student names."""
    event = SimpleNamespace(recipient="Max Koval")

    assert event_matches_student(event, 42, "  MAX   KOVAĽ ")
    assert not event_matches_student(event, 42, "Anna Koval")


def test_prefers_stable_recipient_person_id():
    """An account object's stable ID takes precedence over its name."""
    event = SimpleNamespace(
        recipient=SimpleNamespace(person_id=42, name="Unexpected Name")
    )

    assert event_matches_student(event, "42", "Max Koval")
    assert not event_matches_student(event, 7, "Unexpected Name")
    assert event_recipient(event) == "Unexpected Name"


def test_keeps_events_without_specific_recipient():
    """Unknown and wildcard recipients remain visible for compatibility."""
    assert event_matches_student(SimpleNamespace(), 42, "Max Koval")
    assert event_matches_student(
        SimpleNamespace(recipient="*"), 42, "Max Koval"
    )
    assert event_matches_student(
        SimpleNamespace(recipient="Gesamte Schule"), 42, "Max Koval"
    )


def test_matches_class_and_teaching_group_recipients():
    """Class-wide and subject-group assignments belong to the student."""
    assert event_matches_student(
        SimpleNamespace(recipient="4b"), 42, "Nina Lange", ["4b"]
    )
    assert event_matches_student(
        SimpleNamespace(recipient="4b · Musik"),
        42,
        "Nina Lange",
        ["4b"],
    )
    assert event_matches_student(
        SimpleNamespace(recipient="SLJ 4.A - gramatika"),
        42,
        "Nina Lange",
        ["4.A"],
    )
    assert not event_matches_student(
        SimpleNamespace(recipient="03b"), 42, "Nina Lange", ["4b"]
    )
    assert not event_matches_student(
        SimpleNamespace(recipient="SLJ 14.A - gramatika"),
        42,
        "Nina Lange",
        ["4.A"],
    )


def test_keeps_group_recipient_when_class_metadata_is_unavailable():
    """A failed optional class lookup must not hide valid homework."""
    assert event_matches_student(
        SimpleNamespace(recipient="4b · Musik"), 42, "Nina Lange"
    )
