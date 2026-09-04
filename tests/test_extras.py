"""Tests for the "extras" split-out: meal services, message sending, the
canteen calendar (serving-time handling and always-created entity) and the
term-average helpers.

These mirror the lightweight wrapper pattern from ``test_edupage_wrapper.py``:
``_FakeHass`` runs sync callables inline so the async EduPage wrapper can be
exercised without a live Home Assistant instance. Coordinator-based pieces use
the pytest-homeassistant-custom-component ``hass`` fixture.
"""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant

from custom_components.homeassistantedupage.homeassistant_edupage import (
    Edupage,
    UpdateFailed,
)
from custom_components.homeassistantedupage.sensor import _average, _grade_numeric
from custom_components.homeassistantedupage import _collect_data


class _FakeHass:
    """Minimal hass whose async_add_executor_job runs the sync callable inline."""

    async def async_add_executor_job(self, func, *args, **kwargs):
        return func(*args, **kwargs)


def _wrapper(api):
    wrapper = Edupage(_FakeHass(), sessionid="sess")
    wrapper.api = api
    return wrapper


# ---------------------------------------------------------------------------
# Term-average helpers
# ---------------------------------------------------------------------------


class _Grade:
    def __init__(self, grade_n):
        self.grade_n = grade_n


def test_grade_numeric_handles_numeric_and_invalid_values():
    assert _grade_numeric(1) == 1.0
    assert _grade_numeric("2") == 2.0
    assert _grade_numeric("2,5") == 2.5
    assert _grade_numeric(None) is None
    assert _grade_numeric("abc") is None


def test_average_ignores_non_numeric_grades():
    grades = [_Grade(1), _Grade("2"), _Grade("abc"), _Grade(None), _Grade(3)]
    # 1 + 2 + 3 = 6 / 3 = 2.0 (the "abc"/None entries are skipped)
    assert _average(grades) == 2.0


def test_average_empty_returns_none():
    assert _average([]) is None
    assert _average([_Grade("abc")]) is None


# ---------------------------------------------------------------------------
# Meal services (choose / sign off / rate)
# ---------------------------------------------------------------------------


async def test_choose_meal_calls_meal_choose():
    api = MagicMock()
    meal = MagicMock()
    await _wrapper(api).choose_meal(meal, 3)
    meal.choose.assert_called_once_with(api, 3)


async def test_sign_off_meal_calls_meal_sign_off():
    api = MagicMock()
    meal = MagicMock()
    await _wrapper(api).sign_off_meal(meal)
    meal.sign_off.assert_called_once_with(api)


async def test_rate_meal_calls_rating_rate():
    api = MagicMock()
    rating = MagicMock()
    await _wrapper(api).rate_meal(rating, 8, 9)
    rating.rate.assert_called_once_with(api, 8, 9)


async def test_choose_meal_propagates_failure_as_update_failed():
    api = MagicMock()
    meal = MagicMock()
    meal.choose.side_effect = RuntimeError("boom")
    with pytest.raises(UpdateFailed):
        await _wrapper(api).choose_meal(meal, 3)


# ---------------------------------------------------------------------------
# Message recipient resolution
# ---------------------------------------------------------------------------


class _Account:
    def __init__(self, person_id, name, get_id=None):
        self.person_id = person_id
        self.name = name
        # Mimic edupage-api account objects (``get_id()`` returns e.g. s123/u456).
        self._get_id = get_id or f"s{person_id}"

    def get_id(self):
        return self._get_id


class _ApiWithPeople:
    def get_students(self):
        return [_Account(1, "Alice"), _Account(2, "Bob")]

    def get_teachers(self):
        return [_Account(100, "Mrs Smith")]


async def test_resolve_recipients_by_id_and_name():
    wrapper = _wrapper(_ApiWithPeople())
    resolved = await wrapper.resolve_recipients(["1", "2", "mrs smith"])
    assert {a.person_id for a in resolved} == {1, 2, 100}


async def test_resolve_recipients_raises_on_unknown():
    wrapper = _wrapper(_ApiWithPeople())
    with pytest.raises(UpdateFailed):
        await wrapper.resolve_recipients(["nobody"])


async def test_send_message_passes_recipient_ids_not_objects():
    """send_message must hand the API resolved recipient ID strings, not the
    account objects (edupage-api 0.12.5 rejects EduStudent/EduTeacher subclasses
    via an exact EduAccount type check)."""
    api = MagicMock()
    student = _Account(1, "Alice", get_id="Student123")
    teacher = _Account(2, "Mrs Smith", get_id="Teacher456")
    api.get_students.return_value = [student]
    api.get_teachers.return_value = [teacher]
    wrapper = _wrapper(api)
    await wrapper.send_message(["1", "Mrs Smith"], "hello")
    args, _ = api.send_message.call_args
    assert args[0] == ["Student123", "Teacher456"]
    assert args[1] == "hello"


# ---------------------------------------------------------------------------
# Canteen calendar: serving-time handling and always-created entity
# ---------------------------------------------------------------------------


class _MealType:
    def __init__(self, name):
        self.name = name


class _Meal:
    def __init__(self, meal_type, served_from=None, served_to=None, title="Menu"):
        self.meal_type = _MealType(meal_type)
        self.served_from = served_from
        self.served_to = served_to
        self.title = title


@pytest.fixture
def canteen_calendar(hass: HomeAssistant):
    from custom_components.homeassistantedupage.calendar import EdupageCanteenCalendar

    coord = MagicMock()
    coord.data = {"canteen_menu": {}}
    cal = EdupageCanteenCalendar(coord, {})
    cal.hass = hass
    return cal


def test_map_meal_with_serving_times(canteen_calendar):
    meal = _Meal(
        "LUNCH",
        served_from=datetime(2026, 9, 3, 11, 0),
        served_to=datetime(2026, 9, 3, 13, 0),
    )
    event = canteen_calendar.map_meal_to_calender_event(meal, datetime(2026, 9, 3).date())
    # Duration between the serving window end and start is timezone-independent.
    assert event.end - event.start == timedelta(hours=2)


def test_map_meal_without_serving_times_spans_day(canteen_calendar):
    meal = _Meal("LUNCH", served_from=None, served_to=None)
    day = datetime(2026, 9, 3).date()
    event = canteen_calendar.map_meal_to_calender_event(meal, day)
    # No serving window: the meal occupies the whole day (midnight -> next midnight).
    assert event.end - event.start == timedelta(days=1)


def test_empty_canteen_menu_returns_no_event(canteen_calendar):
    canteen_calendar.coordinator.data = {"canteen_menu": {}}
    assert canteen_calendar.event is None


async def test_always_creates_canteen_calendar(hass: HomeAssistant):
    """The canteen calendar entity must always be created, even with no menu."""
    from custom_components.homeassistantedupage import calendar as calendar_module

    coord = MagicMock()
    coord.data = {"canteen_menu": {}}
    entry = MagicMock()
    hass.data["homeassistantedupage"] = {entry.entry_id: coord}

    added = []
    await calendar_module.async_setup_entry(
        hass, entry, lambda entities: added.extend(entities)
    )

    names = {type(e).__name__ for e in added}
    assert "EdupageCalendar" in names
    assert "EdupageCanteenCalendar" in names


# ---------------------------------------------------------------------------
# Coordinator-level: _collect_data fallback for failing data sections (#94/#93)
# ---------------------------------------------------------------------------


class _OkStudent:
    """Minimal student object used by _collect_data."""

    person_id = 1
    name = "Max"


def _edupage_with_grades_failing():
    """An edupage whose get_grades() raises but everything else succeeds."""
    edupage = MagicMock()
    edupage.get_grades = AsyncMock(side_effect=RuntimeError("non-numeric grade"))
    edupage.get_subjects = AsyncMock(return_value=["Math"])
    edupage.get_notifications = AsyncMock(return_value=[{"id": 1}])
    edupage.get_timetable = AsyncMock(return_value=[])
    edupage.get_meals = AsyncMock(return_value=None)
    edupage.get_timetable_changes = AsyncMock(return_value=[])
    edupage.get_missing_teachers = AsyncMock(return_value=[])
    edupage.get_next_ringing_time = AsyncMock(return_value=None)
    edupage.get_school_year = AsyncMock(return_value=None)
    edupage.get_grades_for_term = AsyncMock(return_value=[])
    return edupage


async def test_collect_data_continues_when_get_grades_fails():
    """A get_grades() crash must not discard student/timetable/subjects data."""
    edupage = _edupage_with_grades_failing()
    student = _OkStudent()

    data = await _collect_data(edupage, student, "Max")

    assert data["grades"] == []
    assert data["student"] == {"id": 1, "name": "Max"}
    assert data["subjects"] == ["Math"]
    assert data["notifications"] == [{"id": 1}]
    assert "timetable" in data
    assert "canteen_menu" in data


async def test_collect_data_continues_when_get_subjects_fails():
    edupage = _edupage_with_grades_failing()
    edupage.get_grades = AsyncMock(return_value=[])
    edupage.get_subjects = AsyncMock(side_effect=IndexError("empty subjects"))

    data = await _collect_data(edupage, _OkStudent(), "Max")

    assert data["subjects"] == []
    assert data["grades"] == []


async def test_collect_data_continues_when_get_notifications_fails():
    edupage = _edupage_with_grades_failing()
    edupage.get_grades = AsyncMock(return_value=[])
    edupage.get_subjects = AsyncMock(return_value=[])
    edupage.get_notifications = AsyncMock(
        side_effect=AttributeError("missing field")
    )

    data = await _collect_data(edupage, _OkStudent(), "Max")

    assert data["notifications"] == []
    assert data["student"]["name"] == "Max"


async def test_collect_data_preserves_student_when_only_grades_fail():
    """The reported #94 case: grades crash, everything else still loads."""
    edupage = _edupage_with_grades_failing()
    edupage.get_timetable = AsyncMock(return_value=[])
    edupage.get_meals = AsyncMock(return_value=None)

    data = await _collect_data(edupage, _OkStudent(), "Max")

    assert data["student"] == {"id": 1, "name": "Max"}
    assert data["grades"] == []
    assert isinstance(data["timetable"], dict)
    assert isinstance(data["canteen_menu"], dict)


async def test_collect_data_records_per_section_freshness():
    """A grades failure sets data_ok['grades']=False while other sections are ok."""
    edupage = _edupage_with_grades_failing()
    data = await _collect_data(edupage, _OkStudent(), "Max")

    ok = data["data_ok"]
    assert ok["grades"] is False
    assert ok["subjects"] is True
    assert ok["notifications"] is True
    assert ok["timetable"] is True
    assert ok["canteen_menu"] is True  # get_meals returned None (success, just empty)
    assert ok["timetable_changes"] is True
    assert ok["missing_teachers"] is True
    assert ok["next_ringing"] is True
    assert ok["school_year"] is False  # get_school_year returned None
    assert ok["grades_per_term"] is False


async def test_collect_data_records_all_sections_ok_when_nothing_fails():
    edupage = _edupage_with_grades_failing()
    edupage.get_grades = AsyncMock(return_value=[])
    edupage.get_school_year = AsyncMock(return_value=2025)
    edupage.get_grades_for_term = AsyncMock(return_value=[])

    data = await _collect_data(edupage, _OkStudent(), "Max")

    ok = data["data_ok"]
    assert ok["grades"] is True
    assert ok["school_year"] is True
    assert ok["grades_per_term"] is True
