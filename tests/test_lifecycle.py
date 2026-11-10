"""Exercise actual HA setup, reauthentication flow and entry reload."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant import config_entries
from homeassistant.exceptions import ConfigEntryAuthFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from edupage_api.registrations import ConsultationOverview
from custom_components.homeassistantedupage.const import DOMAIN
from custom_components.homeassistantedupage.homeassistant_edupage import Edupage, EdupageSessionExpired


@pytest.fixture
def lifecycle_entry(hass):
    entry = MockConfigEntry(domain=DOMAIN, data={
        "username": "parent@example.test", "subdomain": "school", "PHPSESSID": "session",
        "student_id": 1, "student_name": "Student",
    }, unique_id="child-1")
    entry.add_to_hass(hass)
    return entry


def _snapshot():
    return {"student": {"id": 1, "name": "Student"}, "notifications": [],
            "grades": [], "data_ok": {}, "registrations": []}


async def test_expiry_creates_real_reauth_flow_and_blocks_further_network(hass, lifecycle_entry):
    entry = lifecycle_entry
    login = AsyncMock(side_effect=[True, True, EdupageSessionExpired("expired")])
    with patch.object(Edupage, "login", login), patch.object(Edupage, "get_students", AsyncMock(return_value=[SimpleNamespace(person_id=1, name="Student")])), patch(
        "custom_components.homeassistantedupage._collect_data", AsyncMock(return_value=_snapshot())
    ), patch.object(Edupage, "get_consultations", AsyncMock(return_value={"overview": ConsultationOverview([], []), "slots": [], "slots_loaded": []})), patch.object(hass.config_entries, "async_forward_entry_setups", AsyncMock()):
        assert await hass.config_entries.async_setup(entry.entry_id)
        coordinator = hass.data[DOMAIN][entry.entry_id]
        await coordinator.async_request_refresh()
        await hass.async_block_till_done()
        flows = [f for f in hass.config_entries.flow.async_progress()
                 if f["handler"] == DOMAIN and f["context"].get("source") == config_entries.SOURCE_REAUTH]
        assert len(flows) == 1
        assert flows[0]["context"]["entry_id"] == entry.entry_id
        assert not coordinator.last_update_success
        await coordinator.async_request_refresh()
        await hass.async_block_till_done()
        assert login.await_count == 3
        assert len([f for f in hass.config_entries.flow.async_progress()
                    if f["handler"] == DOMAIN]) == 1
        await coordinator.async_shutdown()


async def test_invalid_session_at_setup_creates_real_reauth_flow(hass, lifecycle_entry):
    with patch.object(Edupage, "login", AsyncMock(side_effect=EdupageSessionExpired("expired"))):
        assert not await hass.config_entries.async_setup(lifecycle_entry.entry_id)
        await hass.async_block_till_done()
    assert lifecycle_entry.state == config_entries.ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert any(f["handler"] == DOMAIN and f["context"].get("source") == config_entries.SOURCE_REAUTH for f in flows)


async def test_reload_waits_for_cancelled_consultations_and_can_repeat(hass, lifecycle_entry):
    await hass.async_start()
    entered, cancelled, finish = asyncio.Event(), asyncio.Event(), asyncio.Event()
    old_worker_finished = asyncio.Event()
    reads = 0

    async def consultations(student):
        nonlocal reads
        reads += 1
        if reads == 1:
            entered.set()
            try:
                await finish.wait()
            except asyncio.CancelledError:
                cancelled.set()
                await finish.wait()
                old_worker_finished.set()
                raise
        if reads == 1:
            old_worker_finished.set()
        return {"overview": ConsultationOverview([], []), "slots": [], "slots_loaded": []}

    with patch.object(Edupage, "login", AsyncMock(return_value=True)), patch.object(Edupage, "get_students", AsyncMock(return_value=[SimpleNamespace(person_id=1, name="Student")])), patch.object(Edupage, "get_consultations", AsyncMock(side_effect=consultations)), patch(
        "custom_components.homeassistantedupage._collect_data", AsyncMock(return_value=_snapshot())
    ), patch.object(hass.config_entries, "async_forward_entry_setups", AsyncMock()), patch.object(
        hass.config_entries, "async_forward_entry_unload", AsyncMock(return_value=True)
    ):
        assert await hass.config_entries.async_setup(lifecycle_entry.entry_id)
        await asyncio.wait_for(entered.wait(), 2)
        api = Mock()
        api.login.return_value = None
        api.session.cookies.get_dict.return_value = {"PHPSESSID": "new-session"}
        api.get_students.return_value = [SimpleNamespace(person_id=1)]
        with patch("custom_components.homeassistantedupage.config_flow.Edupage", return_value=api):
            form = await hass.config_entries.flow.async_init(DOMAIN, context={
                "source": config_entries.SOURCE_RECONFIGURE, "entry_id": lifecycle_entry.entry_id,
            })
            result = await hass.config_entries.flow.async_configure(form["flow_id"], {
                "username": "canonical-parent", "password": "never-stored", "subdomain": "school",
            })
        assert result["reason"] == "reconfigured"
        task = hass.data[DOMAIN][lifecycle_entry.entry_id]._startup_consultations_task
        async with asyncio.timeout(2):
            while not task.cancelling():
                await asyncio.sleep(0)
        assert lifecycle_entry.state != config_entries.ConfigEntryState.LOADED
        assert not old_worker_finished.is_set()
        finish.set()
        await hass.async_block_till_done()
        assert old_worker_finished.is_set()
        assert lifecycle_entry.data["PHPSESSID"] == "new-session"
        assert lifecycle_entry.data["username"] == "canonical-parent"
        assert lifecycle_entry.data["student_id"] == 1
        assert "password" not in lifecycle_entry.data
        assert lifecycle_entry.state == config_entries.ConfigEntryState.LOADED
        # None-returning callbacks remain safe when their task has already finished.
        await hass.async_block_till_done()
        assert await hass.config_entries.async_reload(lifecycle_entry.entry_id)
        assert lifecycle_entry.state == config_entries.ConfigEntryState.LOADED


async def test_network_failure_at_setup_retries_without_reauth(hass, lifecycle_entry):
    from homeassistant.helpers.update_coordinator import UpdateFailed

    with patch.object(Edupage, "login", AsyncMock(side_effect=UpdateFailed("network unavailable"))):
        assert not await hass.config_entries.async_setup(lifecycle_entry.entry_id)
        await hass.async_block_till_done()
    assert lifecycle_entry.state == config_entries.ConfigEntryState.SETUP_RETRY
    assert not any(f["handler"] == DOMAIN for f in hass.config_entries.flow.async_progress())


async def test_each_expired_child_gets_one_real_reauth_flow(hass, lifecycle_entry):
    second = MockConfigEntry(domain=DOMAIN, data={**dict(lifecycle_entry.data), "student_id": 2}, unique_id="child-2")
    second.add_to_hass(hass)
    students = [SimpleNamespace(person_id=i, name=f"Student {i}") for i in (1, 2)]
    login = AsyncMock(side_effect=[True] * 4 + [EdupageSessionExpired("expired")] * 2)

    async def collect(api, student, name, **kwargs):
        return {**_snapshot(), "student": {"id": student.person_id, "name": name}}

    with patch.object(Edupage, "login", login), patch.object(Edupage, "get_students", AsyncMock(return_value=students)), patch.object(Edupage, "get_consultations", AsyncMock(return_value={"overview": ConsultationOverview([], []), "slots": [], "slots_loaded": []})), patch(
        "custom_components.homeassistantedupage._collect_data", side_effect=collect,
    ), patch.object(hass.config_entries, "async_forward_entry_setups", AsyncMock()):
        assert await hass.config_entries.async_setup(lifecycle_entry.entry_id)
        await hass.async_block_till_done()
        assert all(entry.state == config_entries.ConfigEntryState.LOADED for entry in (lifecycle_entry, second))
        for entry in (lifecycle_entry, second):
            await hass.data[DOMAIN][entry.entry_id].async_request_refresh()
        await hass.async_block_till_done()
        flows = [f for f in hass.config_entries.flow.async_progress() if f["handler"] == DOMAIN]
        assert {f["context"]["entry_id"] for f in flows} == {lifecycle_entry.entry_id, second.entry_id}
        assert len(flows) == 2
        for entry in (lifecycle_entry, second):
            coordinator = hass.data[DOMAIN][entry.entry_id]
            assert coordinator.data["student"]["id"] == entry.data["student_id"]
            await coordinator.async_request_refresh()
            await coordinator.async_shutdown()
        assert login.await_count == 6
