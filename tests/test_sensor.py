"""Tests for the notification sensor's live ``events`` attribute.

These use the pytest-homeassistant-custom-component ``hass`` fixture and a
DataUpdateCoordinator whose ``data`` is set directly, so the tests exercise the
entity's "read from coordinator.data, not the constructor snapshot" behaviour.
"""

import logging
from datetime import datetime

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from custom_components.homeassistantedupage.sensor import (
    _MAX_EVENTS,
    EduPageNotificationSensor,
)
from custom_components.homeassistantedupage import sensor as sensor_module


class _FakeEvent:
    """Minimal notification event with the fields the sensor reads."""

    def __init__(self, event_id, event_type, text, timestamp, additional_data=None, author=None):
        self.event_id = event_id
        self.event_type = event_type
        self.text = text
        self.timestamp = timestamp
        self.additional_data = additional_data
        self.author = author


class _FakeSubject:
    def __init__(self, subject_id, name):
        self.subject_id = subject_id
        self.name = name


@pytest.fixture
def coordinator(hass: HomeAssistant):
    """A coordinator whose data we can update to simulate polling."""
    coord = DataUpdateCoordinator(hass, logging.getLogger("test"), name="test")
    coord.data = {
        "student": {"id": 1, "name": "Max"},
        "notifications": [],
        "subjects": [
            _FakeSubject(1, "Maths"),
            _FakeSubject(2, "English"),
        ],
    }
    return coord


def _make_sensor(coordinator, notifications=None):
    return EduPageNotificationSensor(
        coordinator,
        student_id=1,
        student_name="Max Kovaľ",
        notifications=notifications if notifications is not None else [],
    )


async def test_events_read_live_data_not_constructor_snapshot(hass, coordinator):
    """The events attribute must reflect coordinator.data updates."""
    sensor = _make_sensor(coordinator, notifications=[])
    # Nothing initially.
    assert sensor.extra_state_attributes["events"] == []

    # Simulate the coordinator polling in new notifications.
    coordinator.data["notifications"] = [
        _FakeEvent(
            event_id=10,
            event_type="homework",
            text="Read chapter 1",
            timestamp=datetime(2026, 9, 1, 8, 0),
            additional_data={"date": "2026-09-05", "predmetid": 1},
            author=_FakeSubject(99, "Teacher"),
        )
    ]
    attrs = sensor.extra_state_attributes
    assert attrs["event_count"] == 1
    assert len(attrs["events"]) == 1
    ev = attrs["events"][0]
    assert ev["id"] == 10
    assert ev["text"] == "Read chapter 1"
    assert ev["deadline"] == "2026-09-05"
    assert ev["subject"] == "Maths"
    # The old flat homework-only attribute is still exposed for compatibility.
    assert attrs["event_1_id"] == 10


async def test_state_count_follows_coordinator(hass, coordinator):
    sensor = _make_sensor(coordinator, notifications=[])
    assert sensor.state == 0
    coordinator.data["notifications"] = [
        _FakeEvent(1, "homework", "a", datetime(2026, 9, 1)),
        _FakeEvent(2, "test", "b", datetime(2026, 9, 2)),
    ]
    assert sensor.state == 2


async def test_defensive_subject_id(hass, coordinator):
    """A missing or non-numeric predmetid must not raise."""
    sensor = _make_sensor(coordinator)
    coordinator.data["notifications"] = [
        _FakeEvent(1, "homework", "a", datetime(2026, 9, 1), additional_data={"predmetid": "not-a-number"}),
        _FakeEvent(2, "homework", "b", datetime(2026, 9, 1), additional_data={}),
    ]
    attrs = sensor.extra_state_attributes
    assert attrs["events"][0]["subject"] is None
    assert attrs["event_1_subject"] is None
    assert "deadline" not in attrs["events"][1]


async def test_events_list_keeps_first_max_events(hass, coordinator):
    """The events attribute keeps the first (newest) _MAX_EVENTS entries."""
    sensor = _make_sensor(coordinator)
    coordinator.data["notifications"] = [
        _FakeEvent(i, "homework", f"n{i}", datetime(2026, 9, 1)) for i in range(_MAX_EVENTS + 20)
    ]
    events = sensor.extra_state_attributes["events"]
    assert len(events) == _MAX_EVENTS
    # EduPage's get_notifications() returns the newest events first, so the
    # first _MAX_EVENTS entries are kept when the list is capped.
    assert events[0]["id"] == 0
    assert events[-1]["id"] == _MAX_EVENTS - 1