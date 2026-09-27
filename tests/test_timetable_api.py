"""Tests for the pinned API's top-level timetable event-name workaround."""

from copy import deepcopy
from datetime import date
from unittest.mock import patch

from edupage_api import Edupage
from edupage_api.exceptions import NotLoggedInException
from edupage_api.people import EduStudent
import pytest

from custom_components.homeassistantedupage.timetable_api import (
    EventNameTimetables,
    TimetableEdupage,
)


def _item(**changes):
    return {
        "type": "event",
        "uniperiod": "",
        "starttime": "00:00",
        "endtime": "24:00",
        "groupnames": [],
        "name": "School holiday",
        **changes,
    }


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({}, "School holiday"),
        ({"name": "  Holiday  "}, "Holiday"),
        ({"name": None}, None),
        ({"name": "   "}, None),
        ({"name": 123}, None),
        ({"type": "lesson"}, None),
        ({"flags": {"event": {"name": "Existing title"}}}, "Existing title"),
        ({"flags": {"dp0": {"note_wd": "Existing curriculum"}}}, "Existing curriculum"),
    ],
)
def test_top_level_name_fallback(changes, expected):
    plan = [_item(**changes)]
    original = deepcopy(plan)
    result = EventNameTimetables(Edupage())._Timetables__parse_timetable(plan)
    assert result.lessons[0].curriculum == expected
    assert plan == original


def test_skipped_headers_do_not_shift_event_names():
    plan = [{"header": []}, _item(), {"header": [{"cmd": "addlesson_t"}]}]
    result = EventNameTimetables(Edupage())._Timetables__parse_timetable(plan)
    assert len(result.lessons) == 1
    assert result.lessons[0].curriculum == "School holiday"


def test_public_timetable_paths_use_compatibility_parser():
    api = TimetableEdupage()
    api.is_logged_in = True
    day = date(2026, 9, 28)
    with patch(
        "edupage_api.timetables.Timetables._Timetables__get_date_plan",
        return_value=[_item()],
    ):
        assert api.get_my_timetable(day).lessons[0].curriculum == "School holiday"

    student = EduStudent(1, "Test Student", None, None, 1, 1)
    with patch(
        "edupage_api.timetables.Timetables._Timetables__get_timetable_data",
        return_value=[_item()],
    ):
        assert api.get_timetable(student, day).lessons[0].curriculum == "School holiday"


def test_unauthenticated_requests_still_fail():
    with pytest.raises(NotLoggedInException):
        TimetableEdupage().get_my_timetable(date(2026, 9, 28))
