"""Tests for the EduPage config flow (with and without 2FA) and reauth.

Uses pytest-homeassistant-custom-component helpers. The edupage_api calls are
mocked so no live network or credentials are required.
"""

from unittest.mock import AsyncMock, MagicMock, patch
from types import SimpleNamespace
from custom_components.homeassistantedupage import _async_update_listener

import pytest

from inspect import signature
from homeassistant import config_entries, data_entry_flow
from homeassistant.core import HomeAssistant

from custom_components.homeassistantedupage.const import (
    CONF_PHPSESSID,
    CONF_STUDENT_ID,
    CONF_STUDENT_NAME,
    CONF_SUBDOMAIN,
    CONF_SUBJECT_IDS,
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

async def test_options_flow_defaults_to_all_available_subjects(
        hass: HomeAssistant,
        config_entry,
):
    """Existing entries default to all currently available subjects."""
    maths = SimpleNamespace(subject_id=1, name="Mathematics")
    english = SimpleNamespace(subject_id=2, name="English")
    coordinator = MagicMock()
    coordinator.data = {"subjects": [maths, english]}
    hass.data[DOMAIN][config_entry.entry_id] = coordinator

    result = await hass.config_entries.options.async_init(
        config_entry.entry_id
    )

    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "init"

    validated = result["data_schema"]({})
    assert set(validated[CONF_SUBJECT_IDS]) == {"1", "2"}


async def test_options_flow_stores_selected_subjects(
        hass: HomeAssistant,
        config_entry,
):
    """The selected subject IDs are stored in the config entry options."""
    maths = SimpleNamespace(subject_id=1, name="Mathematics")
    english = SimpleNamespace(subject_id=2, name="English")
    coordinator = MagicMock()
    coordinator.data = {"subjects": [maths, english]}
    hass.data[DOMAIN][config_entry.entry_id] = coordinator

    result = await hass.config_entries.options.async_init(
        config_entry.entry_id
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_SUBJECT_IDS: ["2"]},
    )

    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert config_entry.options == {CONF_SUBJECT_IDS: ["2"], "convert_letter_grades": False, **{f"letter_grade_{letter}": value for letter, value in zip("abcdef", (1, 2, 3, 4, 5, 5))}}


async def test_options_flow_allows_empty_selection(
        hass: HomeAssistant,
        config_entry,
):
    """Users may disable all per-subject grade sensors."""
    maths = SimpleNamespace(subject_id=1, name="Mathematics")
    coordinator = MagicMock()
    coordinator.data = {"subjects": [maths]}
    hass.data[DOMAIN][config_entry.entry_id] = coordinator

    result = await hass.config_entries.options.async_init(
        config_entry.entry_id
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_SUBJECT_IDS: []},
    )

    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert config_entry.options == {CONF_SUBJECT_IDS: [], "convert_letter_grades": False, **{f"letter_grade_{letter}": value for letter, value in zip("abcdef", (1, 2, 3, 4, 5, 5))}}

async def test_options_update_listener_reloads_entry(
        hass: HomeAssistant,
        config_entry,
):
    """Changing options reloads the config entry."""
    with patch.object(
            hass.config_entries,
            "async_reload",
            new=AsyncMock(return_value=True),
    ) as reload_mock:
        await _async_update_listener(hass, config_entry)

    reload_mock.assert_awaited_once_with(config_entry.entry_id)

async def test_options_flow_stores_custom_letter_conversion(hass, config_entry):
    coordinator = MagicMock()
    coordinator.data = {"subjects": []}
    hass.data[DOMAIN][config_entry.entry_id] = coordinator
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {
        CONF_SUBJECT_IDS: [], "convert_letter_grades": True, "letter_grade_f": 6,
    })
    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert config_entry.options["convert_letter_grades"] is True
    assert config_entry.options["letter_grade_a"] == 1
    assert config_entry.options["letter_grade_f"] == 6


async def test_options_form_schema_serializes_for_frontend(hass, config_entry):
    """The HTTP options endpoint must serialize the entire form schema."""
    import voluptuous_serialize
    from homeassistant.helpers import config_validation as cv

    coordinator = MagicMock()
    coordinator.data = {"subjects": [SimpleNamespace(subject_id=1, name="Inf")]}
    hass.data[DOMAIN][config_entry.entry_id] = coordinator
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    def compatible_serializer(schema):
        value = cv.custom_serializer(schema)
        try:
            import probatio
        except ImportError:
            return value
        return voluptuous_serialize.UNSUPPORTED if value is probatio.UNSUPPORTED else value

    fields = voluptuous_serialize.convert(result["data_schema"], custom_serializer=compatible_serializer)
    assert {field["name"] for field in fields} >= {
        "subject_ids", "convert_letter_grades",
        *[f"letter_grade_{letter}" for letter in "abcdef"],
    }


async def test_options_form_uses_current_ha_serializer(hass, config_entry):
    probatio = pytest.importorskip("probatio")
    from homeassistant.helpers import config_validation as cv
    coordinator = MagicMock()
    coordinator.data = {"subjects": []}
    hass.data[DOMAIN][config_entry.entry_id] = coordinator
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    fields = probatio.to_field_list(result["data_schema"], custom_serializer=cv.custom_serializer)
    assert len(fields) == 8


@pytest.mark.parametrize("value", [float("nan"), float("inf")])
async def test_options_reject_nonfinite_letter_value_without_saving(hass, config_entry, value):
    coordinator = MagicMock()
    coordinator.data = {"subjects": []}
    hass.data[DOMAIN][config_entry.entry_id] = coordinator
    previous = dict(config_entry.options)
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    with pytest.raises(data_entry_flow.InvalidData):
        await hass.config_entries.options.async_configure(result["flow_id"], {
            CONF_SUBJECT_IDS: [], "letter_grade_a": value,
        })
    assert dict(config_entry.options) == previous
