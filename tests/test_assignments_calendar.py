"""Tests for the EduPage homework and exam calendar."""

from datetime import date, datetime
from enum import Enum
import logging
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from custom_components.homeassistantedupage.calendar import (
    EduPageAssignmentsCalendar,
    _parse_notification_date,
)
from custom_components.homeassistantedupage.const import DOMAIN


class _EventType(str, Enum):
    """EduPage event types used in the tests."""

    HOMEWORK = "homework"
    EXAM = "bexam"
    MESSAGE = "sprava"


def _notification(
    event_id,
    *,
    event_type=_EventType.HOMEWORK,
    text="Read chapter 1",
    event_date="2026-09-20",
    subject_id=1,
    author="Mrs Teacher",
    is_done=False,
    recipient=None,
):
    """Create a notification used by the calendar tests."""
    return SimpleNamespace(
        event_id=event_id,
        event_type=event_type,
        text=text,
        additional_data={"date": event_date, "predmetid": subject_id},
        author=SimpleNamespace(name=author) if author else None,
        is_done=is_done,
        recipient=recipient,
    )


@pytest.fixture
def assignments_calendar(hass: HomeAssistant):
    """Return an assignments calendar with student and subject data."""
    coordinator = DataUpdateCoordinator(
        hass,
        logging.getLogger("test"),
        name="test",
        config_entry=None,
    )
    coordinator.data = {
        "student": {
            "id": 1,
            "name": "Max Example",
            "class_names": ["4b"],
        },
        "notifications": [],
        "subjects": [SimpleNamespace(subject_id=1, name="Maths")],
    }
    entity = EduPageAssignmentsCalendar(coordinator, {})
    entity.hass = hass
    return entity


def test_parse_notification_date_accepts_supported_values():
    """EduPage date values are converted to date-only calendar values."""
    assert _parse_notification_date("2026-09-20") == date(2026, 9, 20)
    assert _parse_notification_date("2026-09-20 12:30:00") == date(2026, 9, 20)
    assert _parse_notification_date(datetime(2026, 9, 20, 12, 30)) == date(
        2026, 9, 20
    )
    assert _parse_notification_date("invalid") is None
    assert _parse_notification_date(None) is None


def test_homework_maps_to_all_day_calendar_event(assignments_calendar):
    """Homework becomes an all-day event containing its metadata."""
    event = assignments_calendar._map_notification(_notification(1))

    assert event.start == date(2026, 9, 20)
    assert event.end == date(2026, 9, 21)
    assert event.summary == "[Homework] Maths: Read chapter 1"
    assert event.description == (
        "Type: Homework\nSubject: Maths\nAuthor: Mrs Teacher"
    )


def test_completed_homework_is_marked(assignments_calendar):
    """Completed homework remains in the calendar and is clearly marked."""
    event = assignments_calendar._map_notification(
        _notification(1, is_done=True)
    )

    assert event.summary == "[Completed] [Homework] Maths: Read chapter 1"


def test_exam_types_map_to_exam_event(assignments_calendar):
    """Supported EduPage exam notifications become exam events."""
    event = assignments_calendar._map_notification(
        _notification(2, event_type=_EventType.EXAM, text="Algebra test")
    )

    assert event.summary == "[Exam] Maths: Algebra test"
    assert event.description.startswith("Type: Exam")


def test_unsupported_or_undated_notifications_are_ignored(assignments_calendar):
    """Only dated homework and exam notifications belong in this calendar."""
    assert (
        assignments_calendar._map_notification(
            _notification(2, event_type=_EventType.MESSAGE)
        )
        is None
    )
    assert (
        assignments_calendar._map_notification(
            _notification(3, event_date=None)
        )
        is None
    )


def test_sibling_assignments_are_ignored(assignments_calendar):
    """A calendar contains assignments for its configured student only."""
    assert assignments_calendar._map_notification(
        _notification(1, recipient="4b · Maths")
    ) is not None
    assert assignments_calendar._map_notification(
        _notification(2, recipient="Anna Example")
    ) is None


async def test_get_events_filters_range_and_sorts(assignments_calendar):
    """Calendar queries return only events overlapping the requested range."""
    assignments_calendar.coordinator.data["notifications"] = [
        _notification(3, event_date="2026-09-25"),
        _notification(2, event_type=_EventType.EXAM, event_date="2026-09-20"),
        _notification(1, event_date="2026-09-18"),
    ]
    timezone = ZoneInfo("Europe/Berlin")

    events = await assignments_calendar.async_get_events(
        assignments_calendar.hass,
        datetime(2026, 9, 19, tzinfo=timezone),
        datetime(2026, 9, 22, tzinfo=timezone),
    )

    assert [event.start for event in events] == [date(2026, 9, 20)]
    assert events[0].summary.startswith("[Exam]")


async def test_get_events_supports_partial_day_range(assignments_calendar):
    """An all-day assignment overlaps a query within the same day."""
    assignments_calendar.coordinator.data["notifications"] = [
        _notification(1, event_date="2026-09-20")
    ]
    timezone = ZoneInfo("Europe/Berlin")

    events = await assignments_calendar.async_get_events(
        assignments_calendar.hass,
        datetime(2026, 9, 20, 10, tzinfo=timezone),
        datetime(2026, 9, 20, 12, tzinfo=timezone),
    )

    assert len(events) == 1


def test_next_event_and_device_info(assignments_calendar, monkeypatch):
    """The entity exposes the next dated event and the student device."""
    assignments_calendar.coordinator.data["notifications"] = [
        _notification(2, event_date="2026-09-18"),
        _notification(1, event_date="2026-09-20"),
    ]

    class _Now(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 9, 19, 12, tzinfo=tz)

    monkeypatch.setattr(
        "custom_components.homeassistantedupage.calendar.datetime", _Now
    )

    assert assignments_calendar.event.start == date(2026, 9, 20)
    assert assignments_calendar.unique_id == "edupage_assignments_1"
    assert assignments_calendar.device_info["identifiers"] == {(DOMAIN, "1")}
