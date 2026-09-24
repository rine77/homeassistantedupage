"""Regression tests for the EduPage session-expiration handling.

When the stored PHPSESSID is expired, ``Edupage.login()`` must surface an
``EdupageSessionExpired`` (not a generic ``UpdateFailed``) so the integration's
reauthentication flow is started. Covers the two real-world cases from
homeassistantedupage#70 / #95:

* ``Login.reload_data()`` completes but the api reports ``is_logged_in is False``;
* ``Login.reload_data()`` raises ``IndexError``.
"""

from unittest.mock import MagicMock, patch

import pytest
from edupage_api.exceptions import InsufficientPermissionsException
from homeassistant.helpers.update_coordinator import UpdateFailed
from custom_components.homeassistantedupage.homeassistant_edupage import (
    Edupage,
    EdupageSessionExpired,
)


class _FakeHass:
    """Minimal hass whose async_add_executor_job runs the sync callable inline."""

    async def async_add_executor_job(self, func, *args, **kwargs):
        return func(*args, **kwargs)


def _wrapper(api):
    wrapper = Edupage(_FakeHass(), sessionid="sess")
    wrapper.api = api
    return wrapper


async def test_login_raises_session_expired_when_not_logged_in():
    """reload_data() completes but the session is still not logged in."""
    api = MagicMock()
    api.is_logged_in = False

    with patch(
        "custom_components.homeassistantedupage.homeassistant_edupage.Login",
        return_value=MagicMock(),
    ):
        with pytest.raises(EdupageSessionExpired):
            await _wrapper(api).login("user", "mshviezdoslavova1", "sess")


async def test_login_raises_session_expired_on_index_error():
    """reload_data() raises IndexError (expired session, homeassistantedupage#70)."""
    api = MagicMock()
    api.is_logged_in = True
    login_mock = MagicMock()
    login_mock.reload_data.side_effect = IndexError("list index out of range")

    with patch(
        "custom_components.homeassistantedupage.homeassistant_edupage.Login",
        return_value=login_mock,
    ):
        with pytest.raises(EdupageSessionExpired):
            await _wrapper(api).login("user", "mshviezdoslavova1", "sess")


async def test_login_returns_true_on_valid_session():
    """A healthy reloaded session logs in successfully."""
    api = MagicMock()
    api.is_logged_in = True

    with patch(
        "custom_components.homeassistantedupage.homeassistant_edupage.Login",
        return_value=MagicMock(),
    ):
        result = await _wrapper(api).login("user", "mshviezdoslavova1", "sess")
    assert result is True


async def test_get_meals_returns_none_on_index_error():
    """Empty meal data represented by IndexError is treated as unavailable."""
    api = MagicMock()
    api.get_meals.side_effect = IndexError("list index out of range")

    result = await _wrapper(api).get_meals("2026-09-04")

    assert result is None


async def test_get_meals_returns_none_on_attribute_error():
    """Unsupported meal data represented by AttributeError is treated as unavailable."""
    api = MagicMock()
    api.get_meals.side_effect = AttributeError(
        "'list' object has no attribute 'keys'"
    )

    result = await _wrapper(api).get_meals("2026-09-04")

    assert result is None


async def test_get_meals_raises_update_failed_on_unexpected_error():
    """Unexpected meal API failures remain visible to the coordinator."""
    api = MagicMock()
    api.get_meals.side_effect = RuntimeError("connection failed")

    with pytest.raises(UpdateFailed, match="connection failed"):
        await _wrapper(api).get_meals("2026-09-04")


async def test_get_timetable_uses_direct_result_when_permitted():
    """A working direct timetable request must not switch account context."""
    api = MagicMock()
    student = MagicMock()
    api.get_timetable.return_value = ["lesson"]

    result = await _wrapper(api).get_timetable(student, "2026-09-24")

    assert result == ["lesson"]
    api.get_timetable.assert_called_once_with(student, "2026-09-24")
    api.switch_to_child.assert_not_called()
    api.get_my_timetable.assert_not_called()
    api.switch_to_parent.assert_not_called()


async def test_get_timetable_retries_in_selected_child_context():
    """A permission rejection falls back to the parent child-switch flow."""
    api = MagicMock()
    student = MagicMock()
    api.get_timetable.side_effect = InsufficientPermissionsException(
        "Missing permissions"
    )
    api.get_my_timetable.return_value = ["child lesson"]

    result = await _wrapper(api).get_timetable(student, "2026-09-24")

    assert result == ["child lesson"]
    api.switch_to_child.assert_called_once_with(student)
    api.get_my_timetable.assert_called_once_with("2026-09-24")
    api.switch_to_parent.assert_called_once_with()


async def test_get_timetable_restores_parent_context_when_fallback_fails():
    """The parent context is restored even if the child request fails."""
    api = MagicMock()
    student = MagicMock()
    api.get_timetable.side_effect = InsufficientPermissionsException(
        "Missing permissions"
    )
    api.get_my_timetable.side_effect = RuntimeError("child request failed")

    with pytest.raises(UpdateFailed, match="child request failed"):
        await _wrapper(api).get_timetable(student, "2026-09-24")

    api.switch_to_child.assert_called_once_with(student)
    api.switch_to_parent.assert_called_once_with()


# ---------------------------------------------------------------------------
# Wrapper methods must surface API failures as UpdateFailed
# ---------------------------------------------------------------------------


async def test_get_grades_raises_update_failed_on_api_error():
    api = MagicMock()
    api.get_grades.side_effect = RuntimeError("non-numeric grade")
    with pytest.raises(UpdateFailed, match="get_grades"):
        await _wrapper(api).get_grades()


async def test_get_subjects_raises_update_failed_on_api_error():
    api = MagicMock()
    api.get_subjects.side_effect = IndexError("empty subjects")
    with pytest.raises(UpdateFailed, match="get_subjects"):
        await _wrapper(api).get_subjects()


async def test_get_notifications_raises_update_failed_on_api_error():
    api = MagicMock()
    api.get_notifications.side_effect = AttributeError("missing field")
    with pytest.raises(UpdateFailed, match="get_notifications"):
        await _wrapper(api).get_notifications()
