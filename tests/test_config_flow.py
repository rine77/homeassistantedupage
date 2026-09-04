"""Tests for the EduPage config flow (with and without 2FA) and reauth.

Uses pytest-homeassistant-custom-component helpers. The edupage_api calls are
mocked so no live network or credentials are required.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from inspect import signature
from homeassistant import config_entries, data_entry_flow
from homeassistant.core import HomeAssistant

from custom_components.homeassistantedupage.const import (
    CONF_PHPSESSID,
    CONF_STUDENT_ID,
    CONF_STUDENT_NAME,
    CONF_SUBDOMAIN,
    DOMAIN,
)


def _entry_data():
    # The initial (user) step input: the user types username/password/subdomain.
    return {
        "username": "user",
        "password": "pw",
        "subdomain": "mshviezdoslavova1",
    }


def _stored_entry_data():
    # The persisted config-entry data: no password is stored, only the
    # account identity and the session.
    return {
        "username": "user",
        "subdomain": "mshviezdoslavova1",
    }


def _student():
    s = MagicMock()
    s.person_id = 123
    s.name = "Max Kovaľ"
    return [s]


def _api_logged_in():
    api = MagicMock()
    api.is_logged_in = True
    api.login.return_value = None  # no 2FA required
    api.session.cookies.get_dict.return_value = {"PHPSESSID": "phpsess_123"}
    api.get_students.return_value = _student()
    return api


def _fake_reload_result(self, _entry, data_updates=None, reason=None):
    """Patch target for async_update_reload_and_abort: return a real abort."""
    return self.async_abort(reason=reason)


@pytest.fixture
async def config_entry(hass):
    """A config entry holding a stored (password-free) session, registered on hass.

    Uses the public config_entries.async_add API (with setup stubbed out for the
    whole test) so the entry is registered without depending on a plugin-specific
    mock_config_entry fixture or on Home Assistant's private _entries internals.
    """
    entry = config_entries.ConfigEntry(
        version=1,
        minor_version=1,
        domain=DOMAIN,
        title="Edupage (Max Kovaľ)",
        data={
            **_stored_entry_data(),
            CONF_PHPSESSID: "old",
            CONF_STUDENT_ID: 123,
            CONF_STUDENT_NAME: "Max Kovaľ",
        },
        source="user",
        entry_id="test_entry",
        unique_id="test_unique_id",
        discovery_keys=set(),
        **(
            {"subentries_data": {}}
            if "subentries_data" in signature(config_entries.ConfigEntry).parameters
            else {}
        ),
        options={},
    )
    hass.data.setdefault(DOMAIN, {})
    with patch(
        "homeassistant.config_entries.ConfigEntries.async_setup",
        return_value=AsyncMock(return_value=True),
    ):
        await hass.config_entries.async_add(entry)
        # Keep the patch active while the test runs so the background task
        # triggered by async_add does not attempt a real integration setup.
        yield entry


async def test_async_step_user_without_2fa(hass: HomeAssistant):
    """A normal login (no 2FA) advances to the student-selection step."""
    api = _api_logged_in()

    with patch(
        "custom_components.homeassistantedupage.config_flow.Edupage",
        return_value=api,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input=_entry_data()
        )

    assert result["step_id"] == "select_student"
    assert result["type"] is data_entry_flow.FlowResultType.FORM


async def test_async_step_user_with_2fa(hass: HomeAssistant):
    """When 2FA is required the flow asks for the confirmation code first."""
    api = MagicMock()
    second_factor = MagicMock()
    second_factor.finish_with_code = AsyncMock(return_value=None)
    api.is_logged_in = False
    api.login.return_value = second_factor

    with patch(
        "custom_components.homeassistantedupage.config_flow.Edupage",
        return_value=api,
    ):
        init = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            init["flow_id"], user_input=_entry_data()
        )

    # api.login returned a second-factor object -> two_factor step is shown.
    assert result["step_id"] == "two_factor"
    assert result["type"] is data_entry_flow.FlowResultType.FORM


async def test_reauth_success_aborts_and_reloads_entry(hass: HomeAssistant, config_entry):
    """Reauthentication prompts for the password, updates the PHPSESSID and reloads."""
    entry_id = config_entry.entry_id
    hass.data.setdefault(DOMAIN, {})

    api = _api_logged_in()

    recorded = {}

    def _fake_reload(self, _entry, data_updates=None, reason=None):
        # Capture the exact arguments so we can assert the update+reload path.
        recorded["entry_id"] = _entry.entry_id
        recorded["data_updates"] = data_updates
        recorded["reason"] = reason
        return self.async_abort(reason=reason)

    with patch(
        "custom_components.homeassistantedupage.config_flow.Edupage",
        return_value=api,
    ), patch(
        "homeassistant.config_entries.ConfigFlow.async_update_reload_and_abort",
        _fake_reload,
    ):
        init = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_REAUTH, "entry_id": entry_id},
        )
        # The stored password is not used: the user is prompted to re-enter it.
        assert init["type"] is data_entry_flow.FlowResultType.FORM
        assert init["step_id"] == "reconfigure"
        result = await hass.config_entries.flow.async_configure(
            init["flow_id"],
            user_input={
                "username": "user",
                "password": "pw",
                "subdomain": "mshviezdoslavova1",
            },
        )

    assert result["type"] is data_entry_flow.FlowResultType.ABORT
    # A valid session produces the success reason.
    assert result["reason"] == "reauth_successful"
    # The reload-and-abort path was actually invoked.
    assert recorded == {
        "entry_id": entry_id,
        "data_updates": {CONF_PHPSESSID: "phpsess_123"},
        "reason": "reauth_successful",
    }


async def test_reconfigure_uses_reload(hass: HomeAssistant, config_entry):
    """Reconfiguration prompts for the password and ends in a reload."""
    entry_id = config_entry.entry_id
    hass.data.setdefault(DOMAIN, {})

    api = _api_logged_in()

    with patch(
        "custom_components.homeassistantedupage.config_flow.Edupage",
        return_value=api,
    ), patch(
        "homeassistant.config_entries.ConfigFlow.async_update_reload_and_abort",
        _fake_reload_result,
    ):
        init = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_RECONFIGURE, "entry_id": entry_id},
        )
        # The reconfigure flow also asks the user to re-enter the password.
        assert init["step_id"] == "reconfigure"
        result = await hass.config_entries.flow.async_configure(
            init["flow_id"],
            user_input={
                "username": "user",
                "password": "pw",
                "subdomain": "mshviezdoslavova1",
            },
        )

    assert result["type"] is data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "reconfigured"