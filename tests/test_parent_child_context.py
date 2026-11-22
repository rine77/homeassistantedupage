"""Parent accounts: grades and canteen must be read for the selected child.

EduPage serves ``/znamky/`` and ``/menu/`` for the logged-in user. For a parent
account that is the parent, who has no grades and no boarder id ("Missing
boarder id"), so the wrapper has to switch to the child first.

Also covers schools whose canteen data is keyed under a different name than
the school subdomain, and the numeric ``udalostid`` grade lookup fixed in
EdupageAPI/edupage-api#135.
"""

import datetime
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from edupage_api.exceptions import InvalidMealsData, NotParentException, UnknownServerError
from edupage_api.grades import Grades

from custom_components.homeassistantedupage.homeassistant_edupage import Edupage

DAY = datetime.date(2026, 11, 23)


class _FakeHass:
    async def async_add_executor_job(self, func, *args, **kwargs):
        return func(*args, **kwargs)


def _wrapper(api):
    wrapper = Edupage(_FakeHass(), sessionid="sess")
    wrapper.api = api
    return wrapper


async def test_grades_are_read_in_child_context():
    calls = []
    api = MagicMock()
    api.switch_to_child.side_effect = lambda s: calls.append(("child", s))
    api.get_grades.side_effect = lambda: calls.append(("grades",)) or ["g"]
    api.switch_to_parent.side_effect = lambda: calls.append(("parent",))

    result = await _wrapper(api).get_grades("kid")

    assert result == ["g"]
    assert calls == [("child", "kid"), ("grades",), ("parent",)]


async def test_student_account_is_called_directly():
    api = MagicMock()
    api.switch_to_child.side_effect = NotParentException()
    api.get_grades.return_value = ["g"]

    assert await _wrapper(api).get_grades("kid") == ["g"]
    api.switch_to_parent.assert_not_called()


async def test_failed_switch_back_keeps_the_data():
    api = MagicMock()
    api.get_grades_for_term.return_value = ["g"]
    api.switch_to_parent.side_effect = UnknownServerError()

    assert await _wrapper(api).get_grades_for_term(2026, "P1", "kid") == ["g"]


async def test_no_student_keeps_previous_behaviour():
    api = MagicMock()
    api.get_meals.return_value = None

    assert await _wrapper(api).get_meals(DAY) is None
    api.switch_to_child.assert_not_called()


def _menu_page(key):
    data = {
        key: {
            "novyListok": {
                "2026-11-23": {
                    "2": {
                        "vydaj_od": "11:00",
                        "vydaj_do": "14:29",
                        "druhov_jedal": 2,
                        "isCooking": True,
                        "choosableMenus": {"A": 1},
                        "zmen_do": None,
                        "nazov": "Lunch",
                        "evidencia": {"stav": "A", "obj": "A"},
                        "rows": [{"nazov": "Soup", "alergenyStr": "1", "hmotnostiStr": "200"}],
                    }
                },
                "addInfo": {"stravnikid": 4085},
            }
        }
    }
    return "x edupageData: " + json.dumps(data) + ",\r\n y"


async def test_canteen_keyed_under_other_name_falls_back():
    page = _menu_page("sharedcanteen")
    api = MagicMock()
    api.subdomain = "school"
    api.get_meals.side_effect = InvalidMealsData("Missing boarder id")
    api.session.get.return_value = SimpleNamespace(content=page.encode())

    meals = await _wrapper(api).get_meals(DAY, "kid")

    assert meals.lunch is not None
    assert meals.lunch.ordered_meal == "A"
    assert [m.name for m in meals.lunch.menus] == ["Soup"]
    api.switch_to_child.assert_called_once_with("kid")


def test_numeric_udalostid_is_matched():
    data = {
        "vsetkyZnamky": [{"udalostid": 555, "datum": "2026-11-10 08:00:00", "data": "1"}],
        "vsetkyUdalosti": {
            "edupage": {
                "555": {
                    "p_meno": "Test",
                    "PredmetID": "7",
                    "UcitelID": None,
                    "p_typ_udalosti": "1",
                    "p_vaha": "20",
                }
            }
        },
    }
    page = ".znamkyStudentViewer(" + json.dumps(data) + ");\r\n\t\t});\r\n\t\t</script>"
    response = SimpleNamespace(content=page.encode())
    edupage = SimpleNamespace(
        subdomain="school",
        is_logged_in=True,
        session=SimpleNamespace(get=lambda url: response, post=lambda url: response),
        data={"dbi": {"subjects": {"7": {"short": "MAT"}}}},
    )

    grades = Grades(edupage).get_grades(None, None)

    assert len(grades) == 1
    assert grades[0].subject_name == "MAT"
