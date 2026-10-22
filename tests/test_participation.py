"""Read-only participation summaries and child isolation."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from custom_components.homeassistantedupage import _collect_data
from custom_components.homeassistantedupage.homeassistant_edupage import Edupage
from custom_components.homeassistantedupage.participation import (
    free_slot,
    registration_open,
    registration_summary,
)
from custom_components.homeassistantedupage.sensor import EduPageParticipationSensor
from edupage_api.exceptions import NotParentException
from edupage_api.registrations import (
    Consultation,
    ConsultationOverview,
    ConsultationReservation,
    ConsultationSlot,
    Registration,
    RegistrationSelection,
    RegistrationTarget,
)
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

NOW = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)


def action(**kwargs):
    return Registration("1", "Survey", active=True, votable=True, **kwargs)


def block():
    return Consultation(
        "c1",
        "t1",
        "Teacher",
        NOW + timedelta(days=1),
        NOW + timedelta(days=1, hours=2),
        30,
        None,
        NOW + timedelta(hours=1),
    )


class FakeHass:
    config = SimpleNamespace(time_zone="Europe/Prague")

    async def async_add_executor_job(self, func, *args):
        return func(*args)


def wrapper():
    result = Edupage(FakeHass())
    result.api = Mock()
    return result


def test_open_registration_dates_and_server_eligibility():
    assert registration_open(action(), NOW)
    assert not registration_open(action(closes_at=NOW - timedelta(seconds=1)), NOW)
    assert not registration_open(action(opens_at=NOW + timedelta(seconds=1)), NOW)
    closed = action()
    closed.votable = False
    assert not registration_open(closed, NOW)


def test_summary_excludes_answers_and_bounds_title():
    item = action(
        targets=[RegistrationTarget("q1", "Q", {"private": "question"})],
        selection=[RegistrationSelection("q1", "answered", {"private": "answer"})],
    )
    item.name = "x" * 1000
    summary = registration_summary(item, "school")
    assert summary["question_count"] == 1
    assert len(summary["title"]) == 200
    assert "private" not in str(summary)
    assert summary["selection_states"] == ["answered"]


def test_slot_deadline_closed_and_unknown_are_not_available():
    b = block()
    slot = ConsultationSlot("c1", NOW + timedelta(days=1), "free", False)
    assert free_slot(slot, {"c1": b}, NOW)
    assert not free_slot(slot, {}, NOW)
    b.booking_closes_at = NOW - timedelta(seconds=1)
    assert not free_slot(slot, {"c1": b}, NOW)
    b.booking_closes_at = None
    b.teaches_student = True
    slot.status = "unknown"
    assert not free_slot(slot, {"c1": b}, NOW)
    slot.status, slot.closed = "free", True
    assert not free_slot(slot, {"c1": b}, NOW)


async def test_wrapper_restores_parent_on_detail_failure():
    w = wrapper()
    w.api.get_registrations.side_effect = RuntimeError("failure")
    student = SimpleNamespace(person_id=1)
    with pytest.raises(UpdateFailed):
        await w.get_registrations(student)
    w.api.switch_to_child.assert_called_once_with(student)
    w.api.switch_to_parent.assert_called_once()


async def test_wrapper_student_account_and_read_only_slot_calls():
    w = wrapper()
    w.api.switch_to_child.side_effect = NotParentException()
    w.api.get_consultations.return_value = ConsultationOverview([block()], [])
    w.api.get_consultation_slots.return_value = []
    from freezegun import freeze_time

    with freeze_time(NOW):
        result = await w.get_consultations(SimpleNamespace(person_id=1))
    assert result["slots"] == []
    w.api.get_consultation_slots.assert_called_once_with("c1", "Europe/Prague")
    w.api.switch_to_parent.assert_not_called()
    assert {c[0] for c in w.api.mock_calls} == {
        "switch_to_child",
        "get_consultations",
        "get_consultation_slots",
    }


async def test_coordinator_optional_failures_do_not_discard_grades():
    api = AsyncMock()
    api.get_grades.return_value = ["grade"]
    api.get_registrations.side_effect = RuntimeError("unavailable")
    api.get_consultations.return_value = {
        "overview": ConsultationOverview([], []),
        "slots": [],
    }
    api.get_timetable.return_value = None
    api.get_meals.return_value = None
    api.get_classes.return_value = []
    result = await _collect_data(api, SimpleNamespace(person_id=1), "Student")
    assert result["grades"] == ["grade"]
    assert not result["data_ok"]["registrations"]
    assert result["registrations"] is None
    assert result["data_ok"]["consultations"]


async def test_participation_sensor_counts_bounds_and_outage(hass):
    import logging

    coordinator = DataUpdateCoordinator(
        hass,
        logging.getLogger(__name__),
        name="test",
        config_entry=MockConfigEntry(
            domain="homeassistantedupage", data={"subdomain": "school"}
        ),
    )
    coordinator.async_set_updated_data(
        {
            "registrations": [action() for _ in range(30)],
            "data_ok": {"registrations": True},
        }
    )
    sensor = EduPageParticipationSensor(coordinator, 1, "Student", "open_registrations")
    assert sensor.state == 30
    assert len(sensor.extra_state_attributes["items"]) == 20
    coordinator.async_set_updated_data(
        {"registrations": None, "data_ok": {"registrations": False}}
    )
    assert sensor.state == 30
    assert sensor.extra_state_attributes["data_stale"]


async def test_slot_and_booking_sensor_counts(hass):
    import logging

    from homeassistant.util import dt as dt_util

    now = dt_util.now()
    b = block()
    b.booking_closes_at = None
    b.teaches_student = True
    future = now + timedelta(days=1)
    coordinator = DataUpdateCoordinator(
        hass, logging.getLogger(__name__), name="test", config_entry=None
    )
    coordinator.async_set_updated_data(
        {
            "consultations": {
                "overview": ConsultationOverview(
                    [b], [ConsultationReservation("c1", future)]
                ),
                "slots": [
                    ConsultationSlot("c1", future, "free", False),
                    ConsultationSlot("c1", future, "temporary", True),
                ],
            },
            "data_ok": {"consultations": True},
        }
    )
    for kind in ("free_consultation_slots", "own_consultations"):
        sensor = EduPageParticipationSensor(coordinator, 1, "Student", kind)
        assert sensor.state == 1
        assert len(sensor.extra_state_attributes["items"]) == 1


async def test_initial_collection_defers_consultations():
    api = AsyncMock()
    api.get_grades.return_value = []
    api.get_subjects.return_value = []
    api.get_students.return_value = []
    api.get_school_year.return_value = 2026
    api.get_timetable.return_value = None
    api.get_meals.return_value = None
    result = await _collect_data(api, SimpleNamespace(person_id=1), "Student", include_consultations=False)
    api.get_consultations.assert_not_awaited()
    api.get_registrations.assert_awaited_once()
    assert result["consultations"] is None
    assert result["data_ok"]["consultations"] is False


async def test_background_consultations_preserve_grades_and_serialize(hass):
    import asyncio
    from custom_components.homeassistantedupage import _load_startup_consultations
    grades = [object()]
    result = {"overview": ConsultationOverview([], []), "slots": [], "slots_loaded": []}
    lock = asyncio.Lock()
    async def read(student):
        assert lock.locked()
        return result
    coordinator = SimpleNamespace(
        fetch_lock=lock, data={"grades": grades, "data_ok": {"grades": True}},
        edupage=SimpleNamespace(get_consultations=AsyncMock(side_effect=read)),
        async_set_updated_data=Mock(),
    )
    await _load_startup_consultations(coordinator, SimpleNamespace(person_id=1))
    updated = coordinator.async_set_updated_data.call_args.args[0]
    assert updated["grades"] is grades
    assert updated["consultations"] is result
    assert updated["data_ok"] == {"grades": True, "consultations": True}


async def test_cancelled_background_read_keeps_child_lock_until_restored():
    import asyncio
    from custom_components.homeassistantedupage import _load_startup_consultations
    entered, finish = asyncio.Event(), asyncio.Event()
    lock = asyncio.Lock()
    async def read(student):
        entered.set()
        await finish.wait()
        assert lock.locked()
        return {}
    coordinator = SimpleNamespace(fetch_lock=lock, data={},
        edupage=SimpleNamespace(get_consultations=AsyncMock(side_effect=read)),
        async_set_updated_data=Mock())
    task = asyncio.create_task(_load_startup_consultations(coordinator, None))
    await entered.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert lock.locked()
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not lock.locked()
    coordinator.async_set_updated_data.assert_not_called()
