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