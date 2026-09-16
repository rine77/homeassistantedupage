"""Tests for homework and exam summary sensors."""

from datetime import date
from enum import Enum
import logging
from types import SimpleNamespace

import pytest
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from custom_components.homeassistantedupage.const import DOMAIN
from custom_components.homeassistantedupage.sensor import (
    EduPageAssignmentSensor,
    EduPageNextHomeworkDeadlineSensor,
    EduPageOpenHomeworkSensor,
    EduPageOverdueHomeworkSensor,
    EduPageUpcomingExamsSensor,
    async_setup_entry,
)


class _EventType(str, Enum):
    """EduPage event types used by the sensors."""

    HOMEWORK = "homework"
    EXAM = "bexam"
    ORAL_EXAM = "oexam"
    MESSAGE = "sprava"


def _notification(
    event_id,
    *,
    event_type=_EventType.HOMEWORK,
    due="2026-09-20",
    subject_id=1,
    text="Read chapter 1",
    is_done=False,
    recipient=None,
):
    """Create a notification used by the sensor tests."""
    return SimpleNamespace(
        event_id=event_id,
        event_type=event_type,
        additional_data={"date": due, "predmetid": subject_id},
        text=text,
        is_done=is_done,
        recipient=recipient,
    )


@pytest.fixture
def coordinator(hass: HomeAssistant):
    """Return coordinator data suitable for assignment sensors."""
    coord = DataUpdateCoordinator(
        hass,
        logging.getLogger("test"),
        name="test",
        config_entry=None,
    )
    coord.data = {
        "student": {
            "id": 1,
            "name": "Max Example",
            "class_names": ["4b"],
        },
        "notifications": [],
        "subjects": [SimpleNamespace(subject_id=1, name="Maths")],
        "data_ok": {"notifications": True},
    }
    coord.last_update_success = True
    return coord


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    """Use a stable local date for deadline calculations."""
    monkeypatch.setattr(
        EduPageAssignmentSensor,
        "_today",
        staticmethod(lambda: date(2026, 9, 20)),
    )


def test_open_homework_counts_incomplete_items(coordinator):
    """Open count includes incomplete dated and undated homework only."""
    coordinator.data["notifications"] = [
        _notification(1),
        _notification(2, due=None),
        _notification(3, is_done=True),
        _notification(4, event_type=_EventType.EXAM),
    ]

    sensor = EduPageOpenHomeworkSensor(coordinator, 1, "Max Example")

    assert sensor.state == 2
    assert sensor.extra_state_attributes == {"data_stale": False}


def test_assignment_sensors_exclude_sibling_notifications(coordinator):
    """Homework and exams are scoped to the configured student."""
    coordinator.data["notifications"] = [
        _notification(1, recipient="4b · Maths"),
        _notification(2, recipient="Anna Example"),
        _notification(
            3, event_type=_EventType.EXAM, recipient="Max Example"
        ),
        _notification(
            4, event_type=_EventType.EXAM, recipient="Anna Example"
        ),
    ]

    homework = EduPageOpenHomeworkSensor(coordinator, 1, "Max Example")
    exams = EduPageUpcomingExamsSensor(coordinator, 1, "Max Example")

    assert homework.state == 1
    assert exams.state == 1


def test_overdue_homework_excludes_today_and_completed(coordinator):
    """Only incomplete homework before today is overdue."""
    coordinator.data["notifications"] = [
        _notification(1, due="2026-09-19"),
        _notification(2, due="2026-09-20"),
        _notification(3, due="2026-09-18", is_done=True),
        _notification(4, due="invalid"),
    ]

    sensor = EduPageOverdueHomeworkSensor(coordinator, 1, "Max Example")

    assert sensor.state == 1


def test_next_deadline_exposes_homework_details(coordinator):
    """The nearest future deadline includes useful dashboard attributes."""
    coordinator.data["notifications"] = [
        _notification(1, due="2026-09-23", text="Later task"),
        _notification(2, due="2026-09-21", text="Nearest task"),
        _notification(3, due="2026-09-19", text="Overdue task"),
        _notification(4, due="2026-09-20", is_done=True),
    ]
    sensor = EduPageNextHomeworkDeadlineSensor(
        coordinator, 1, "Max Example"
    )

    assert sensor.state == date(2026, 9, 21)
    assert sensor.device_class == SensorDeviceClass.DATE
    assert sensor.extra_state_attributes == {
        "data_stale": False,
        "text": "Nearest task",
        "subject": "Maths",
        "days_remaining": 1,
    }


def test_next_deadline_is_unknown_when_none_is_upcoming(coordinator):
    """A fresh empty result clears an earlier next deadline."""
    sensor = EduPageNextHomeworkDeadlineSensor(
        coordinator, 1, "Max Example"
    )
    coordinator.data["notifications"] = [_notification(1, due="2026-09-21")]
    assert sensor.state == date(2026, 9, 21)

    coordinator.data["notifications"] = []

    assert sensor.state is None
    assert sensor._last_value is None
    assert sensor.extra_state_attributes == {"data_stale": False}


def test_upcoming_exams_count_today_and_future(coordinator):
    """Past and undated exams are excluded from the upcoming count."""
    coordinator.data["notifications"] = [
        _notification(1, event_type=_EventType.EXAM, due="2026-09-19"),
        _notification(2, event_type=_EventType.EXAM, due="2026-09-20"),
        _notification(3, event_type=_EventType.ORAL_EXAM, due="2026-09-25"),
        _notification(4, event_type=_EventType.EXAM, due=None),
        _notification(5, event_type=_EventType.MESSAGE),
    ]

    sensor = EduPageUpcomingExamsSensor(coordinator, 1, "Max Example")

    assert sensor.state == 2


def test_count_sensor_keeps_last_value_when_notifications_fail(coordinator):
    """A notification outage does not reset a useful count to zero."""
    sensor = EduPageOpenHomeworkSensor(coordinator, 1, "Max Example")
    coordinator.data["notifications"] = [_notification(1)]
    assert sensor.state == 1

    coordinator.data["notifications"] = []
    coordinator.data["data_ok"]["notifications"] = False

    assert sensor.state == 1
    assert sensor.extra_state_attributes == {"data_stale": True}


def test_entities_share_the_student_device(coordinator):
    """Assignment sensors are grouped under the existing student device."""
    sensor = EduPageOpenHomeworkSensor(coordinator, 1, "Max Example")

    assert sensor.unique_id == "edupage_open_homework_1"
    assert sensor.device_info["identifiers"] == {(DOMAIN, "1")}
    assert sensor.device_info["name"] == "EduPage - Max Example"


async def test_setup_adds_all_assignment_sensors(hass, coordinator):
    """The sensor platform registers all four assignment summaries."""
    coordinator.data.update(
        {
            "grades": [],
            "timetable_changes": [],
            "missing_teachers": [],
            "next_ringing": None,
            "grades_per_term": {"first": [], "second": []},
        }
    )
    entry = SimpleNamespace(
        entry_id="assignment-sensors",
        data={"student_id": 1, "student_name": "Max Example"},
        options={},
    )
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    added = []

    await async_setup_entry(
        hass,
        entry,
        lambda entities, update_before_add=False: added.extend(entities),
    )

    assignment_types = {
        type(entity)
        for entity in added
        if isinstance(entity, EduPageAssignmentSensor)
    }
    assert assignment_types == {
        EduPageOpenHomeworkSensor,
        EduPageOverdueHomeworkSensor,
        EduPageNextHomeworkDeadlineSensor,
        EduPageUpcomingExamsSensor,
    }
    assert all(
        entity.device_info["identifiers"] == {(DOMAIN, "1")}
        for entity in added
    )
    assert all(
        entity.device_info["name"] == "EduPage - Max Example"
        for entity in added
    )
