"""Tests for the read-only EduPage homework to-do entity."""

from datetime import date, datetime
from enum import Enum
import logging
from types import SimpleNamespace

import pytest
from homeassistant.components.todo import TodoItemStatus
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from custom_components.homeassistantedupage.const import DOMAIN
from custom_components.homeassistantedupage.todo import (
    EduPageHomeworkTodoEntity,
    _parse_due,
)


class _EventType(str, Enum):
    HOMEWORK = "homework"
    MESSAGE = "sprava"


def _event(
    event_id,
    *,
    event_type=_EventType.HOMEWORK,
    text="Read chapter 1",
    due="2026-09-20",
    subject_id=1,
    author="Mrs Teacher",
    is_done=False,
    done_at=None,
    recipient=None,
):
    """Create a timeline event used by the tests."""
    return SimpleNamespace(
        event_id=event_id,
        event_type=event_type,
        text=text,
        additional_data={"date": due, "predmetid": subject_id},
        author=SimpleNamespace(name=author) if author else None,
        is_done=is_done,
        done_at=done_at,
        recipient=recipient,
    )


@pytest.fixture
def coordinator(hass: HomeAssistant):
    """Return coordinator data suitable for homework tests."""
    coord = DataUpdateCoordinator(
        hass,
        logging.getLogger("test"),
        name="test",
        config_entry=None,
    )
    coord.data = {
        "student": {"id": 1, "name": "Max Example"},
        "notifications": [],
        "subjects": [SimpleNamespace(subject_id=1, name="Maths")],
    }
    return coord


def _entity(coordinator):
    return EduPageHomeworkTodoEntity(coordinator, 1, "Max Example")


def test_parse_due_accepts_supported_values():
    """Deadlines are exposed as date-only values accepted by Home Assistant."""
    assert _parse_due("2026-09-20") == date(2026, 9, 20)
    assert _parse_due("2026-09-20 12:30:00") == date(2026, 9, 20)
    assert _parse_due(datetime(2026, 9, 20, 12, 30)) == date(2026, 9, 20)
    assert _parse_due("invalid") is None
    assert _parse_due(None) is None


def test_homework_is_mapped_to_todo_item(coordinator):
    """Homework metadata is mapped to native Home Assistant fields."""
    coordinator.data["notifications"] = [_event(10)]

    item = _entity(coordinator).todo_items[0]

    assert item.uid == "10"
    assert item.summary == "Maths: Read chapter 1"
    assert item.status == TodoItemStatus.NEEDS_ACTION
    assert item.due == date(2026, 9, 20)
    assert item.description == "Subject: Maths\nAuthor: Mrs Teacher"


def test_completed_homework_uses_completed_status(coordinator):
    """EduPage completion state is preserved."""
    coordinator.data["notifications"] = [
        _event(
            11,
            is_done=True,
            done_at=datetime(2026, 9, 19, 17, 30),
        )
    ]

    item = _entity(coordinator).todo_items[0]

    assert item.status == TodoItemStatus.COMPLETED


def test_only_valid_homework_events_are_included(coordinator):
    """Other timeline entries and homework without IDs are ignored."""
    coordinator.data["notifications"] = [
        _event(1),
        _event(2, event_type=_EventType.MESSAGE),
        _event(None),
    ]

    assert [item.uid for item in _entity(coordinator).todo_items] == ["1"]


def test_only_homework_for_selected_student_is_included(coordinator):
    """A parent account's sibling homework is excluded from this list."""
    coordinator.data["notifications"] = [
        _event(1, recipient="Max Example"),
        _event(2, recipient="Anna Example"),
        _event(3, recipient="*"),
    ]

    assert [item.uid for item in _entity(coordinator).todo_items] == ["1", "3"]


def test_items_are_live_and_sorted_open_first_by_due_date(coordinator):
    """The list follows coordinator updates and has a useful stable order."""
    entity = _entity(coordinator)
    assert entity.todo_items == []

    coordinator.data["notifications"] = [
        _event(3, due="2026-09-25", is_done=True),
        _event(2, due="2026-09-22"),
        _event(1, due="2026-09-18"),
    ]

    assert [item.uid for item in entity.todo_items] == ["1", "2", "3"]
    assert entity.state == 2


def test_entity_is_read_only_and_uses_student_device(coordinator):
    """No modifying services are advertised and the device ID is stable."""
    entity = _entity(coordinator)

    assert entity.supported_features == 0
    assert entity.device_info["identifiers"] == {(DOMAIN, "1")}
    assert entity.device_info["name"] == "EduPage - Max Example"
