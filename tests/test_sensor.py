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
from custom_components.homeassistantedupage.sensor import (
    _MAX_EVENTS,
    EduPageNotificationSensor,
    EduPageSubjectSensor,
)

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

class _FakeGrade:
    """Minimal grade object with the fields used by the subject sensor."""

    def __init__(
            self,
            grade_n,
            title,
            date,
            *,
            teacher=None,
            comment=None,
            percent=None,
            max_points=None,
            class_grade_avg=None,
            subject_id=1,
    ):
        self.grade_n = grade_n
        self.title = title
        self.date = date
        self.teacher = teacher
        self.comment = comment
        self.percent = percent
        self.max_points = max_points
        self.class_grade_avg = class_grade_avg
        self.subject_id = subject_id

@pytest.fixture
def coordinator(hass: HomeAssistant):
    """A coordinator whose data we can update to simulate polling."""
    coord = DataUpdateCoordinator(
        hass,
        logging.getLogger("test"),
        name="test",
        config_entry=None,
    )
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


async def test_flat_event_attributes_are_bounded(hass, coordinator):
    """The flat event_N_* attributes must also be capped at _MAX_EVENTS."""
    sensor = _make_sensor(coordinator)
    coordinator.data["notifications"] = [
        _FakeEvent(i, "homework", f"n{i}", datetime(2026, 9, 1))
        for i in range(_MAX_EVENTS + 20)
    ]
    attrs = sensor.extra_state_attributes
    # Only the first _MAX_EVENTS events are emitted as flat attributes.
    assert attrs[f"event_{_MAX_EVENTS}_id"] == _MAX_EVENTS - 1
    assert f"event_{_MAX_EVENTS + 1}_id" not in attrs

def test_latest_grade_uses_date_not_api_order(coordinator):
    """The latest grade must be selected by date from an unsorted API result."""
    older = _FakeGrade(
        2,
        "Older test",
        datetime(2026, 8, 20, 8, 0),
        teacher=_FakeSubject(10, "Mrs Older"),
    )
    latest = _FakeGrade(
        1,
        "Latest test",
        datetime(2026, 9, 3, 10, 30),
        teacher=_FakeSubject(11, "Mr Latest"),
        comment="Excellent",
        percent=95,
        max_points=20,
        class_grade_avg=2.4,
    )
    coordinator.data["grades"] = [latest, older]

    sensor = EduPageSubjectSensor(
        coordinator,
        student_id=1,
        student_name="Max Kovaľ",
        subject_name="Maths",
        subject_id=1,
        grades=[],
    )

    attrs = sensor.extra_state_attributes

    assert attrs["latest_grade"] == 1
    assert attrs["latest_grade_title"] == "Latest test"
    assert attrs["latest_grade_date"] == "2026-09-03 10:30:00"
    assert attrs["latest_grade_teacher"] == "Mr Latest"
    assert attrs["latest_grade_comment"] == "Excellent"
    assert attrs["latest_grade_percent"] == 95
    assert attrs["latest_grade_max_points"] == 20
    assert attrs["latest_grade_class_avg_grade"] == 2.4


def test_latest_grade_omits_missing_optional_fields(coordinator):
    """Optional latest-grade attributes are omitted when EduPage has no value."""
    coordinator.data["grades"] = [
        _FakeGrade(
            3,
            "Short test",
            datetime(2026, 9, 2, 9, 0),
        )
    ]

    sensor = EduPageSubjectSensor(
        coordinator,
        student_id=1,
        student_name="Max Kovaľ",
        subject_name="Maths",
        subject_id=1,
        grades=[],
    )

    attrs = sensor.extra_state_attributes

    assert attrs["latest_grade"] == 3
    assert attrs["latest_grade_title"] == "Short test"
    assert attrs["latest_grade_date"] == "2026-09-02 09:00:00"
    assert "latest_grade_teacher" not in attrs
    assert "latest_grade_comment" not in attrs
    assert "latest_grade_percent" not in attrs
    assert "latest_grade_max_points" not in attrs
    assert "latest_grade_class_avg_grade" not in attrs
