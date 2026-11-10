"""Tests for EduPage behaviour-note helpers."""

from types import SimpleNamespace

import pytest

from custom_components.homeassistantedupage.note_helpers import (
    note_attributes,
    note_matches_student,
)


def test_note_label_is_used_when_text_is_missing():
    event = SimpleNamespace(
        text=None,
        additional_data={"typ": "disrespecting", "typ_text": "Label"},
    )

    assert note_attributes(event) == {
        "text": "Label",
        "note_type": "disrespecting",
        "note_label": "Label",
    }


def test_existing_text_is_kept():
    event = SimpleNamespace(text="Custom", additional_data={"typ_text": "Label"})

    assert note_attributes(event)["text"] == "Custom"


def test_missing_additional_data_is_tolerated():
    event = SimpleNamespace(text="null", additional_data=None)

    assert note_attributes(event) == {
        "text": None,
        "note_type": None,
        "note_label": None,
    }


def _note(recipient):
    return SimpleNamespace(text=None, additional_data={}, recipient=recipient)


@pytest.mark.parametrize(
    "recipient",
    [
        None,
        "",
        "*",
        "Celá škola",
        "Whole school",
        "5.A",
        "5.A - Group 1",
        "Rodičia Margarétky",
        "Other Child",
        "Max",
        "Max Example Junior",
    ],
)
def test_note_is_not_attributed_to_missing_or_ambiguous_recipients(recipient):
    """Personal notes are never attributed unless the recipient is this student."""
    assert note_matches_student(_note(recipient), 1, "Max Example") is False


@pytest.mark.parametrize("recipient", ["Max Example", "  max   example ", "Máx Exámple"])
def test_note_is_attributed_to_exact_student_name(recipient):
    """Name matching ignores case, accents and extra whitespace."""
    assert note_matches_student(_note(recipient), 1, "Max Example") is True


def test_note_recipient_person_id_takes_precedence():
    """A recipient object with a person ID is matched by ID, not by name."""
    same = SimpleNamespace(person_id=1, name="Someone Else")
    other = SimpleNamespace(person_id=2, name="Max Example")

    assert note_matches_student(_note(same), 1, "Max Example") is True
    assert note_matches_student(_note(other), 1, "Max Example") is False


def test_note_without_student_name_is_not_attributed():
    assert note_matches_student(_note("Max Example"), 1, None) is False
