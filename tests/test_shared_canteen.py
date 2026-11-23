"""Canteens whose data is not keyed under the school subdomain.

Some schools share a canteen with other schools. EduPage then stores the
canteen under a different key in ``edupageData``, and edupage-api raises
``InvalidMealsData("Missing boarder id")``. The wrapper falls back to the
entry that has both a boarder id and menu data for the requested day.
"""

import datetime
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from edupage_api.exceptions import InvalidMealsData

from custom_components.homeassistantedupage.homeassistant_edupage import Edupage

DAY = datetime.date(2026, 11, 23)


class _FakeHass:
    async def async_add_executor_job(self, func, *args, **kwargs):
        return func(*args, **kwargs)


def _wrapper(api):
    wrapper = Edupage(_FakeHass(), sessionid="sess")
    wrapper.api = api
    return wrapper


def _lunch(soup):
    return {
        "2": {
            "vydaj_od": "11:00",
            "vydaj_do": "14:29",
            "druhov_jedal": 2,
            "isCooking": True,
            "choosableMenus": {"A": 1},
            "zmen_do": None,
            "nazov": "Lunch",
            "evidencia": {"stav": "A", "obj": "A"},
            "rows": [{"nazov": soup, "alergenyStr": "1", "hmotnostiStr": "200"}],
        }
    }


def _canteen(soup="Soup", boarder_id=4085, day="2026-11-23"):
    return {"novyListok": {day: _lunch(soup), "addInfo": {"stravnikid": boarder_id}}}


def _fallback_api(data):
    page = "x edupageData: " + json.dumps(data) + ",\r\n y"
    api = MagicMock()
    api.subdomain = "school"
    api.get_meals.side_effect = InvalidMealsData("Missing boarder id")
    api.session.get.return_value = SimpleNamespace(content=page.encode())
    return api


async def test_regular_canteen_is_unchanged():
    api = MagicMock()
    api.get_meals.return_value = "meals"

    assert await _wrapper(api).get_meals(DAY) == "meals"
    api.session.get.assert_not_called()


async def test_canteen_keyed_under_other_name_falls_back():
    api = _fallback_api({"sharedcanteen": _canteen()})

    meals = await _wrapper(api).get_meals(DAY)

    assert meals.lunch is not None
    assert meals.lunch.ordered_meal == "A"
    assert [m.name for m in meals.lunch.menus] == ["Soup"]


async def test_fallback_skips_empty_and_incomplete_entries():
    api = _fallback_api(
        {
            # Empty menu and no boarder id.
            "school": {"novyListok": {}},
            # Menu for the day but no boarder id.
            "othercanteen": {"novyListok": {"2026-11-23": _lunch("No boarder")}},
            # Boarder id but no menu for the requested day.
            "wrongday": _canteen(soup="Wrong day", day="2026-11-24"),
            # The valid entry.
            "sharedcanteen": _canteen(soup="Goulash"),
        }
    )

    meals = await _wrapper(api).get_meals(DAY)

    assert meals.lunch is not None
    assert [m.name for m in meals.lunch.menus] == ["Goulash"]


async def test_fallback_without_valid_entry_returns_none():
    api = _fallback_api(
        {
            "school": {"novyListok": {}},
            "othercanteen": {"novyListok": {"2026-11-23": _lunch("No boarder")}},
        }
    )

    assert await _wrapper(api).get_meals(DAY) is None
