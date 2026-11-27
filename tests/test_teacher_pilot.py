"""Teacher setup, session identity and the read-only calendar."""

from datetime import datetime, time, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
import voluptuous as vol
from homeassistant import config_entries, data_entry_flow
from homeassistant.helpers import entity_registry as er

from ha_test_helpers import MockConfigEntry

from custom_components.homeassistantedupage import _resolve_coordinator
from custom_components.homeassistantedupage.const import (
    CONF_PHPSESSID,
    CONF_TEACHER_ACCOUNT,
    CONF_TEACHER_USER_ID,
    DOMAIN,
)

_IDENTITY = {"username": "example", "subdomain": "school"}
_USER_ID = "example-teacher-id"


def _mock_api(user_id=_USER_ID):
    api = MagicMock()
    api.is_logged_in = True
    api.login.return_value = None
    api.get_user_id.return_value = user_id
    api.session.cookies.get_dict.return_value = {CONF_PHPSESSID: "fresh"}
    return api


async def test_teacher_setup_skips_student_selection(hass):
    api = _mock_api()
    with patch("custom_components.homeassistantedupage.config_flow.Edupage", return_value=api), patch.object(
        hass.config_entries, "async_setup", new=AsyncMock(return_value=True)
    ):
        form = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        result = await hass.config_entries.flow.async_configure(
            form["flow_id"],
            user_input={**_IDENTITY, "password": "secret", CONF_TEACHER_ACCOUNT: True},
        )
        await hass.async_block_till_done()

    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["data"] == {**_IDENTITY, CONF_PHPSESSID: "fresh", CONF_TEACHER_USER_ID: _USER_ID}
    api.get_students.assert_not_called()


@pytest.mark.parametrize("same_account", [True, False])
async def test_teacher_reauth_validates_the_existing_identity(hass, same_account):
    data = {**_IDENTITY, CONF_PHPSESSID: "old", CONF_TEACHER_USER_ID: _USER_ID}
    entry = MockConfigEntry(domain=DOMAIN, data=data)
    entry.add_to_hass(hass)
    api = _mock_api(_USER_ID if same_account else "different-account")
    with patch("custom_components.homeassistantedupage.config_flow.Edupage", return_value=api), patch.object(
        hass.config_entries, "async_schedule_reload"
    ) as reload_entry:
        form = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_REAUTH, "entry_id": entry.entry_id}, data=data,
        )
        assert form["step_id"] == "reconfigure"
        assert {key.schema for key in form["data_schema"].schema} == {"password"}
        result = await hass.config_entries.flow.async_configure(
            form["flow_id"], user_input={"password": "secret"}
        )

    assert result["type"] is data_entry_flow.FlowResultType.ABORT
    if same_account:
        assert result["reason"] == "reauth_successful"
        assert entry.data[CONF_PHPSESSID] == "fresh"
        reload_entry.assert_called_once_with(entry.entry_id)
    else:
        assert result["reason"] == "teacher_account_mismatch"
        assert entry.data[CONF_PHPSESSID] == "old"
        reload_entry.assert_not_called()
    api.get_students.assert_not_called()


async def test_teacher_entry_only_loads_own_timetable_and_unloads(hass):
    hass.config.time_zone = "Europe/Bratislava"
    local_tz = ZoneInfo(hass.config.time_zone)
    today = datetime.now(local_tz).date()
    lesson = SimpleNamespace(
        start_time=time(8), end_time=time(8, 45), is_cancelled=False,
        teachers=[], classes=[SimpleNamespace(name="6A")],
        classrooms=[SimpleNamespace(name="Room 21")],
        subject=SimpleNamespace(name="Maths"),
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**_IDENTITY, CONF_PHPSESSID: "stored", CONF_TEACHER_USER_ID: _USER_ID},
    )
    entry.add_to_hass(hass)
    api = _mock_api()
    api.get_my_timetable.side_effect = lambda day: [lesson] if day == today else []

    def reload_session(_wrapper, _subdomain, _sessionid, _username):
        """Inject a session without contacting the server."""

    with patch("custom_components.homeassistantedupage.homeassistant_edupage.APIEdupage", return_value=api), patch(
        "custom_components.homeassistantedupage.homeassistant_edupage.Edupage._load_session", reload_session
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        entities = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
        assert len(entities) == 1
        entity_id = entities[0].entity_id
        assert entity_id.startswith("calendar.")
        assert not entry.supports_options

        response = await hass.services.async_call(
            "calendar", "get_events",
            {"entity_id": entity_id, "start_date_time": datetime.combine(today, time.min, local_tz),
             "end_date_time": datetime.combine(today + timedelta(days=1), time.min, local_tz)},
            blocking=True, return_response=True,
        )
        (event,) = response[entity_id]["events"]
        assert event["summary"] == "Maths"
        assert event["location"] == "Room 21"
        assert "6A" in event["description"]
        assert datetime.fromisoformat(event["start"]) == datetime.combine(today, time(8), local_tz)

        with pytest.raises(vol.Invalid):
            _resolve_coordinator(hass, SimpleNamespace(data={"entry_id": entry.entry_id}))
        student = object()
        with patch.dict(hass.data[DOMAIN], {"student-entry": student}):
            assert _resolve_coordinator(hass, SimpleNamespace(data={})) is student

        assert await hass.config_entries.async_unload(entry.entry_id)
        assert entry.entry_id not in hass.data[DOMAIN]

    api.get_my_timetable.assert_called()
    api.get_students.assert_not_called()
    api.get_grades.assert_not_called()
    api.get_meals.assert_not_called()
