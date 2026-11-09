"""Tests for EduPage behaviour-note helpers."""

from types import SimpleNamespace

from custom_components.homeassistantedupage.note_helpers import note_attributes


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
