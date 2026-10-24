"""Regression tests for the edupage-api 0.13.0 -> 0.13.3 upgrade.

These pin the behaviours that changed across the jump, so a future dependency
bump that reverts or alters one of them fails here rather than on a user's
install. Each test names the upstream change it guards.

What changed between the two versions that this integration has to care about:

- ``Login.reload_data()`` moved to ``LoginSession.reload_data()`` (0.13.1)
- ``DbiHelper`` reads an empty array-shaped item group as an empty mapping
- ``Lunches.get_meals()`` returns ``None`` where 0.13.0 raised ``IndexError``
- ``Timetables.get_my_timetable()`` refuses a parent account that has not
  selected a child (0.13.3, via #129)
- ``EduGrade`` gained ``importance`` and now always populates ``max_points``
"""

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from edupage_api import Edupage, LoginSession
from edupage_api.dbi import DbiHelper
from edupage_api.exceptions import InsufficientPermissionsException
from edupage_api.grades import EduGrade

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.homeassistantedupage.const import CONF_PHPSESSID, DOMAIN
from custom_components.homeassistantedupage.homeassistant_edupage import (
    Edupage as Wrapper,
)

DAY = date(2026, 9, 28)


def _wrapper(hass, api):
    """A Wrapper around a stub API, bypassing __init__'s hass requirement."""
    wrapper = Wrapper.__new__(Wrapper)
    wrapper.hass = hass
    wrapper.api = api
    wrapper.sessionid = ""
    return wrapper


# ---------------------------------------------------------------------------
# the dependency itself
# ---------------------------------------------------------------------------


def test_library_reads_empty_dbi_groups():
    """The reason for the bump: the library copes, so we carry no workaround."""
    api = Edupage()
    api.is_logged_in = True
    api.subdomain = "example"
    api.data = {"dbi": {"classrooms": [], "teachers": {}, "subjects": []}}

    assert DbiHelper(api).fetch_classroom_number("42") is None
    assert DbiHelper(api).fetch_classroom_list() == {}


def test_session_api_lives_on_login_session():
    """`Login.reload_data` was removed in 0.13.1; LoginSession carries it now."""
    assert hasattr(LoginSession, "reload_data")
    assert hasattr(LoginSession, "logout")
    assert hasattr(LoginSession, "parse_rpc_response")
    assert hasattr(LoginSession, "parse_login_data")


def test_grade_fields_include_importance():
    """The subject sensor reads `importance`, which 0.13.3 added."""
    import dataclasses

    names = {f.name for f in dataclasses.fields(EduGrade)}
    assert {"importance", "max_points"} <= names


def test_grade_type_missing_becomes_the_string_none():
    """Documents an upstream wart rather than working around it.

    0.13.3 does `str(details.get("p_typ_udalosti"))`, so an absent field yields
    the string "None" instead of None. The integration only reports it as an
    attribute, so nothing breaks; this test makes the behaviour explicit so a
    future bump that changes it is noticed.
    """
    import edupage_api
    import pathlib

    source = pathlib.Path(edupage_api.__file__).parent / "grades.py"
    assert 'str(details.get("p_typ_udalosti"))' in source.read_text()


def test_wrapper_reloads_the_session_through_login_session(hass: HomeAssistant):
    """`_load_session` must call the class that still exists in 0.13.3."""
    seen = {}

    class _FakeLoginSession:
        def __init__(self, target):
            self._target = target

        def reload_data(self, subdomain, session_id, username):
            seen["args"] = (subdomain, session_id, username)
            # EduPage sends an empty array for a group with no entries.
            self._target.data = {"dbi": {"classrooms": []}}

    wrapper = _wrapper(hass, Edupage())
    wrapper.api.is_logged_in = True

    with patch(
        "custom_components.homeassistantedupage.homeassistant_edupage.LoginSession",
        _FakeLoginSession,
    ):
        wrapper._load_session("school", "sess", "user")

    assert seen["args"] == ("school", "sess", "user")
    assert DbiHelper(wrapper.api).fetch_classroom_number("42") is None


# ---------------------------------------------------------------------------
# get_meals() returning None
# ---------------------------------------------------------------------------


async def test_get_meals_none_is_passed_through(hass: HomeAssistant):
    """0.13.3 returns None for a menu the server did not serve.

    0.13.0 raised IndexError for the same response and the wrapper already
    converted that to None, so the observable result must not change.
    """
    api = MagicMock()
    api.get_meals.return_value = None

    assert await _wrapper(hass, api).get_meals(DAY) is None


async def test_get_meals_index_error_still_becomes_none(hass: HomeAssistant):
    """The older IndexError path keeps working, for older servers."""
    api = MagicMock()
    api.get_meals.side_effect = IndexError("list index out of range")

    assert await _wrapper(hass, api).get_meals(DAY) is None


async def test_get_meals_other_errors_still_surface(hass: HomeAssistant):
    """A genuine failure is still turned into UpdateFailed, not swallowed."""
    api = MagicMock()
    api.get_meals.side_effect = ValueError("boom")

    with pytest.raises(UpdateFailed):
        await _wrapper(hass, api).get_meals(DAY)


# ---------------------------------------------------------------------------
# parent timetable
# ---------------------------------------------------------------------------


async def test_timetable_selects_a_child_before_the_fallback(hass: HomeAssistant):
    """0.13.3 refuses a parent timetable unless a child is selected first.

    The wrapper only reaches `get_my_timetable` after `switch_to_child`, so the
    new MissingDataException is never triggered.
    """
    api = MagicMock()
    api.get_timetable.side_effect = InsufficientPermissionsException("no")
    api.get_my_timetable.return_value = "timetable"

    student = MagicMock()
    result = _wrapper(hass, api)._get_timetable(student, DAY)

    api.switch_to_child.assert_called_once_with(student)
    api.get_my_timetable.assert_called_once_with(DAY)
    api.switch_to_parent.assert_called_once_with()
    assert result == "timetable"


async def test_timetable_switches_back_even_when_the_retry_fails(hass: HomeAssistant):
    """The selected-child context is never left behind on failure."""
    api = MagicMock()
    api.get_timetable.side_effect = InsufficientPermissionsException("no")
    api.get_my_timetable.side_effect = ValueError("boom")

    with pytest.raises(ValueError):
        _wrapper(hass, api)._get_timetable(MagicMock(), DAY)

    api.switch_to_parent.assert_called_once_with()


# ---------------------------------------------------------------------------
# logout
# ---------------------------------------------------------------------------


async def test_wrapper_logout_clears_the_stored_session_id(hass: HomeAssistant):
    api = MagicMock()
    wrapper = _wrapper(hass, api)
    wrapper.sessionid = "phpsess_123"

    await wrapper.logout()

    api.logout.assert_called_once_with()
    assert wrapper.sessionid == ""


def _entry(**data):
    entry = MagicMock()
    entry.entry_id = data.pop("entry_id", "entry_1")
    entry.data = {
        CONF_PHPSESSID: "phpsess_123",
        "username": "user",
        "subdomain": "school",
        **data,
    }
    return entry


def _register(hass, *entries):
    """Put entries into hass.config_entries for the sibling-session checks."""
    return patch.object(
        hass.config_entries, "async_entries", return_value=list(entries)
    )


async def test_entry_removal_invalidates_the_session(hass: HomeAssistant):
    """Deleting the account logs out instead of leaving a session dangling."""
    from custom_components.homeassistantedupage import async_remove_entry

    entry = _entry()
    edupage = MagicMock()
    edupage.login = AsyncMock()
    edupage.logout = AsyncMock()

    with (
        _register(hass, entry),
        patch("custom_components.homeassistantedupage.Edupage", return_value=edupage),
    ):
        await async_remove_entry(hass, entry)

    edupage.login.assert_awaited_once_with("user", "school", "phpsess_123")
    edupage.logout.assert_awaited_once_with()


async def test_removal_keeps_a_session_a_sibling_entry_uses(hass: HomeAssistant):
    """Two students on one EduPage account share a PHPSESSID.

    Removing one entry must not invalidate the session the other still relies
    on, or that entry starts failing with an expired session.
    """
    from custom_components.homeassistantedupage import async_remove_entry

    removed = _entry(entry_id="entry_1", **{CONF_PHPSESSID: "shared"})
    sibling = _entry(entry_id="entry_2", **{CONF_PHPSESSID: "shared"})

    with (
        _register(hass, removed, sibling),
        patch("custom_components.homeassistantedupage.Edupage") as edupage_cls,
    ):
        await async_remove_entry(hass, removed)

    edupage_cls.assert_not_called()


async def test_removal_logs_out_when_a_sibling_has_a_different_session(
    hass: HomeAssistant,
):
    """A sibling on the same school but its own session is not a blocker."""
    from custom_components.homeassistantedupage import async_remove_entry

    removed = _entry(entry_id="entry_1", **{CONF_PHPSESSID: "mine"})
    sibling = _entry(entry_id="entry_2", **{CONF_PHPSESSID: "theirs"})

    edupage = MagicMock()
    edupage.login = AsyncMock()
    edupage.logout = AsyncMock()

    with (
        _register(hass, removed, sibling),
        patch("custom_components.homeassistantedupage.Edupage", return_value=edupage),
    ):
        await async_remove_entry(hass, removed)

    edupage.logout.assert_awaited_once_with()


async def test_removal_does_not_treat_itself_as_a_sibling(hass: HomeAssistant):
    """The entry being removed must not block its own cleanup.

    HA still lists the entry while `async_remove_entry` runs, so the guard has
    to exclude it by entry_id rather than merely by being present.
    """
    from custom_components.homeassistantedupage import async_remove_entry

    entry = _entry(entry_id="entry_1")

    edupage = MagicMock()
    edupage.login = AsyncMock()
    edupage.logout = AsyncMock()

    with (
        _register(hass, entry),
        patch("custom_components.homeassistantedupage.Edupage", return_value=edupage),
    ):
        await async_remove_entry(hass, entry)

    edupage.logout.assert_awaited_once_with()


async def test_entry_removal_survives_an_expired_session(hass: HomeAssistant):
    """A session that is already dead must not block removing the entry."""
    from custom_components.homeassistantedupage import async_remove_entry

    edupage = MagicMock()
    edupage.login = AsyncMock(side_effect=Exception("expired"))

    with patch("custom_components.homeassistantedupage.Edupage", return_value=edupage):
        await async_remove_entry(hass, _entry())  # must not raise


async def test_entry_removal_without_a_session_is_a_no_op(hass: HomeAssistant):
    from custom_components.homeassistantedupage import async_remove_entry

    entry = _entry()
    entry.data = {"username": "user", "subdomain": "school"}

    with patch("custom_components.homeassistantedupage.Edupage") as edupage_cls:
        await async_remove_entry(hass, entry)

    edupage_cls.assert_not_called()


async def test_unload_does_not_log_out(hass: HomeAssistant):
    """Unloading must leave the stored session usable for the next load.

    `async_unload_entry` runs on every restart, so logging out there would kill
    the credential the entry relies on.
    """
    from custom_components.homeassistantedupage import async_unload_entry

    entry = MagicMock()
    entry.entry_id = "abc"
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN]["abc"] = MagicMock()

    with patch.object(
        hass.config_entries, "async_forward_entry_unload", AsyncMock(return_value=True)
    ):
        assert await async_unload_entry(hass, entry) is True

    assert "abc" not in hass.data[DOMAIN]