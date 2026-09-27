"""Regression tests for timetable event titles."""

from datetime import date, datetime, time
import logging

from edupage_api import Edupage
from edupage_api.subjects import Subject
from edupage_api.timetables import Lesson, Timetables
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
import pytest

from custom_components.homeassistantedupage.calendar import EdupageCalendar


DAY = date(2026, 9, 28)
EVENT_TITLE = "Svátek: Den české státnosti"


@pytest.fixture
def timetable_calendar(hass):
    """Create the timetable calendar without connecting to EduPage."""
    coordinator = DataUpdateCoordinator(
        hass, logging.getLogger("test"), name="test", config_entry=None
    )
    coordinator.data = {"student": {"id": 1, "name": "Test Student"}}
    entity = EdupageCalendar(coordinator, {})
    entity.hass = hass
    return entity


def _lesson(*, subject=None, curriculum=None, is_event=True, is_cancelled=False):
    return Lesson(
        period=None,
        start_time=time(0),
        end_time=time(23, 59),
        duration=1,
        subject=subject,
        classes=None,
        groups=None,
        teachers=None,
        classrooms=None,
        curriculum=curriculum,
        online_lesson_link=None,
        is_cancelled=is_cancelled,
        is_event=is_event,
    )


@pytest.mark.parametrize("is_cancelled", [False, True])
async def test_edupage_event_name_reaches_calendar(
    timetable_calendar, freezer, is_cancelled
):
    """Use the installed API parser to cover the event-name data contract."""
    timetable = Timetables(Edupage())._Timetables__parse_timetable(
        [{
            "type": "event",
            "uniperiod": "",
            "starttime": "00:00",
            "endtime": "24:00",
            "groupnames": [],
            "removed": is_cancelled,
            "flags": {"event": {"name": EVENT_TITLE}},
        }]
    )
    timetable_calendar.coordinator.data["timetable"] = {DAY: timetable}
    events = await timetable_calendar.async_get_events(
        timetable_calendar.hass,
        datetime(2026, 9, 28),
        datetime(2026, 9, 29),
    )

    expected = ("[Canceled] " if is_cancelled else "") + EVENT_TITLE
    assert len(events) == 1
    assert events[0].summary == expected
    freezer.move_to("2026-09-28 12:00:00")
    assert timetable_calendar.event.summary == expected


@pytest.mark.parametrize("curriculum", [None, "", "   "])
def test_unnamed_event_keeps_fallback(timetable_calendar, curriculum):
    event = timetable_calendar.map_lesson_to_calender_event(
        _lesson(curriculum=curriculum), DAY
    )
    assert event.summary == "Unknown Subject"


def test_event_title_is_trimmed(timetable_calendar):
    event = timetable_calendar.map_lesson_to_calender_event(
        _lesson(curriculum=f"  {EVENT_TITLE}  "), DAY
    )
    assert event.summary == EVENT_TITLE


@pytest.mark.parametrize("is_event", [False, True])
def test_subject_name_takes_precedence(timetable_calendar, is_event):
    event = timetable_calendar.map_lesson_to_calender_event(
        _lesson(
            subject=Subject(1, "Matematika", "MAT"),
            curriculum="Algebra",
            is_event=is_event,
        ),
        DAY,
    )
    assert event.summary == "Matematika"


def test_regular_lesson_does_not_use_curriculum_as_subject(timetable_calendar):
    event = timetable_calendar.map_lesson_to_calender_event(
        _lesson(curriculum="Algebra", is_event=False), DAY
    )
    assert event.summary == "Unknown Subject"
