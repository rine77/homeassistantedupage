"""Tests for the EduPage event entity."""

from datetime import datetime
from enum import Enum
import logging
from types import SimpleNamespace

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from custom_components.homeassistantedupage.event import (
    EVENT_NEW_EXAM,
    EVENT_NEW_GRADE,
    EVENT_NEW_HOMEWORK,
    EduPageEventEntity,
)


class _EventType(str, Enum):
    HOMEWORK = "homework"


@pytest.fixture
def coordinator(hass: HomeAssistant):
    """Return coordinator data suitable for event entity tests."""
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


def _event(event_id, event_type, **kwargs):
    return SimpleNamespace(
        event_id=event_id,
        event_type=event_type,
        text=kwargs.get("text", "Example"),
        timestamp=kwargs.get("timestamp", datetime(2026, 9, 14, 8, 0)),
        author=kwargs.get("author"),
        additional_data=kwargs.get("additional_data", {}),
        is_done=kwargs.get("is_done", False),
        is_starred=kwargs.get("is_starred", False),
    )


def _entity(coordinator):
    return EduPageEventEntity(coordinator, 1, "Max Example")


def test_existing_notifications_form_initial_baseline(coordinator):
    """Notifications present during setup must not be replayed."""
    coordinator.data["notifications"] = [_event(1, "znamka")]
    entity = _entity(coordinator)
    triggered = []
    entity._trigger_event = lambda event_type, data: triggered.append(
        (event_type, data)
    )
    entity.async_write_ha_state = lambda: None

    entity._handle_coordinator_update()

    assert triggered == []


def test_new_supported_notifications_are_triggered_oldest_first(coordinator):
    """Every supported new timeline item is emitted exactly once."""
    entity = _entity(coordinator)
    triggered = []
    entity._trigger_event = lambda event_type, data: triggered.append(
        (event_type, data)
    )
    entity.async_write_ha_state = lambda: None

    # EduPage returns newest first.
    coordinator.data["notifications"] = [
        _event(3, "rexam"),
        _event(2, _EventType.HOMEWORK),
        _event(1, "znamka"),
    ]
    entity._handle_coordinator_update()
    entity._handle_coordinator_update()

    assert [event_type for event_type, _data in triggered] == [
        EVENT_NEW_GRADE,
        EVENT_NEW_HOMEWORK,
        EVENT_NEW_EXAM,
    ]


def test_unsupported_notifications_are_not_triggered_or_retried(coordinator):
    """Unknown timeline types are ignored and added to the baseline."""
    entity = _entity(coordinator)
    triggered = []
    entity._trigger_event = lambda event_type, data: triggered.append(
        (event_type, data)
    )
    entity.async_write_ha_state = lambda: None

    coordinator.data["notifications"] = [_event(10, "news")]
    entity._handle_coordinator_update()
    entity._handle_coordinator_update()

    assert triggered == []
    assert 10 in entity._known_event_ids


def test_event_attributes_are_structured_and_serializable(coordinator):
    """Event metadata contains resolved subjects and ISO timestamps."""
    entity = _entity(coordinator)
    event = _event(
        20,
        _EventType.HOMEWORK,
        text="Read chapter 1",
        author=SimpleNamespace(name="Mrs Teacher"),
        additional_data={"predmetid": 1, "date": "2026-09-16"},
        is_starred=True,
    )

    attributes = entity._event_attributes(event, "homework")

    assert attributes["event_id"] == 20
    assert attributes["student_name"] == "Max Example"
    assert attributes["subject"] == "Maths"
    assert attributes["author"] == "Mrs Teacher"
    assert attributes["deadline"] == "2026-09-16"
    assert attributes["timestamp"] == "2026-09-14T08:00:00"
    assert attributes["is_starred"] is True


def test_event_entity_creates_student_device(coordinator):
    """The event entity belongs to a stable per-student HA device."""
    entity = _entity(coordinator)

    assert entity.device_info["identifiers"] == {
        ("homeassistantedupage", "1")
    }
    assert entity.device_info["name"] == "EduPage - Max Example"
