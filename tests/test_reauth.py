"""Tests for the reauthentication trigger when the stored PHPSESSID expires.

``async_setup_entry`` must start a reauthentication flow via
``entry.async_start_reauth()`` — not the nonexistent
``hass.config_entries.async_reauth()`` — in two situations:

* the stored session is found expired during initial setup;
* the stored session expires during a later coordinator refresh.
"""

from inspect import signature
from unittest.mock import AsyncMock, patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant

from custom_components.homeassistantedupage import async_setup_entry
from custom_components.homeassistantedupage.const import (
    CONF_PHPSESSID,
    CONF_STUDENT_ID,
    CONF_STUDENT_NAME,
    DOMAIN,
)
from custom_components.homeassistantedupage.homeassistant_edupage import (
    Edupage,
    EdupageSessionExpired,
)


def _student():
    s = AsyncMock()
    s.person_id = 1
    s.name = "Test"
    return s


def _entry():
    data = {
        "username": "user",
        "subdomain": "s",
        CONF_PHPSESSID: "p",
        CONF_STUDENT_ID: 1,
        CONF_STUDENT_NAME: "Test",
    }
    kwargs = dict(
        version=1,
        minor_version=1,
        domain="homeassistantedupage",
        title="Edupage (Test)",
        data=data,
        source="user",
        entry_id="reauth-test-entry",
        unique_id="reauth-test-unique",
        discovery_keys=set(),
        options={},
    )
    if "subentries_data" in signature(config_entries.ConfigEntry).parameters:
        kwargs["subentries_data"] = {}
    return config_entries.ConfigEntry(**kwargs)


async def test_expired_session_during_setup_starts_reauth(hass: HomeAssistant):
    entry = _entry()
    reauth = AsyncMock()
    entry.async_start_reauth = reauth

    with patch.object(
        Edupage,
        "login",
        new=AsyncMock(side_effect=EdupageSessionExpired("expired")),
    ):
        result = await async_setup_entry(hass, entry)

    assert result is False
    reauth.assert_awaited_once()


async def test_expired_session_during_refresh_starts_reauth(hass: HomeAssistant):
    entry = _entry()
    entry._async_set_state(
        hass,
        config_entries.ConfigEntryState.SETUP_IN_PROGRESS,
        None,
    )
    reauth = AsyncMock()
    entry.async_start_reauth = reauth

    login_calls = {"n": 0}

    async def flaky_login(self, username, subdomain, sessionid):
        login_calls["n"] += 1
        if login_calls["n"] > 2:
            raise EdupageSessionExpired("expired during refresh")
        return None

    login_p = patch.object(Edupage, "login", new=flaky_login)
    students_p = patch.object(
        Edupage,
        "get_students",
        new=AsyncMock(return_value=[_student()]),
    )
    fwd_p = patch.object(
        hass.config_entries,
        "async_forward_entry_setups",
        new=AsyncMock(return_value=None),
    )
    login_p.start()
    students_p.start()
    fwd_p.start()
    coordinator = None
    try:
        result = await async_setup_entry(hass, entry)

        assert result is True
        reauth.assert_not_awaited()

        coordinator = hass.data[DOMAIN][entry.entry_id]
        await coordinator.async_request_refresh()

        reauth.assert_awaited_once()
    finally:
        if coordinator is not None:
            await coordinator.async_shutdown()
        fwd_p.stop()
        students_p.stop()
        login_p.stop()
