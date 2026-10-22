"""Consultation UI, actions, calendars, events and scheduling without live writes."""

import logging
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from custom_components.homeassistantedupage.button import ConsultationButton
from custom_components.homeassistantedupage.calendar import EduPageConsultationCalendar
from custom_components.homeassistantedupage.consultation_services import (
    async_setup_services,
    perform_action,
)
from custom_components.homeassistantedupage.participation import (
    block_summaries,
    consultation_item,
    registration_summary,
)
from custom_components.homeassistantedupage.participation_monitor import (
    ParticipationTracker,
    setup_refresh_schedule,
)
from custom_components.homeassistantedupage.select import ConsultationSelect
from custom_components.homeassistantedupage.sensor import EduPageParticipationSensor
from edupage_api.exceptions import ConsultationUncertain
from edupage_api.registrations import (
    Consultation,
    ConsultationOverview,
    ConsultationReservation,
    ConsultationSlot,
    Registration,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from pytest_homeassistant_custom_component.common import MockConfigEntry

NOW = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)


def block(id="b1", teacher="t1", teaches=True, **kwargs):
    return Consultation(
        id,
        teacher,
        "Teacher " + teacher,
        NOW + timedelta(days=3),
        NOW + timedelta(days=3, hours=1),
        30,
        NOW - timedelta(hours=1),
        NOW + timedelta(days=2),
        note="Note",
        teaches_student=teaches,
        **kwargs,
    )


def data(blocks=None, slots=None, bookings=None):
    return {
        "consultations": {
            "overview": ConsultationOverview(blocks or [block()], bookings or []),
            "slots": slots or [],
            "slots_loaded": [b.consultation_id for b in (blocks or [block()])],
        },
        "registrations": [],
        "data_ok": {"consultations": True, "registrations": True},
    }


def coordinator(hass, values=None, options=None):
    entry = MockConfigEntry(
        domain="homeassistantedupage",
        data={"student_id": 1, "student_name": "Student", "subdomain": "school"},
        options=options or {},
    )
    c = DataUpdateCoordinator(
        hass, logging.getLogger(__name__), name="test", config_entry=entry
    )
    c.async_set_updated_data(values or data())
    c.fetch_lock = __import__("asyncio").Lock()
    c.edupage = SimpleNamespace(
        get_students=AsyncMock(return_value=[SimpleNamespace(person_id=1)]),
        consultation_action=AsyncMock(return_value={"reserve": {}}),
    )
    c.async_request_refresh = AsyncMock()
    hass.data["homeassistantedupage"] = {entry.entry_id: c}
    return c, entry


async def test_child_filter_and_full_blocks(hass):
    a, b = block(), block("b2", "t2", False)
    c, _ = coordinator(
        hass, data([a, b], [ConsultationSlot("b2", b.starts_at, "free", False)])
    )
    summaries = block_summaries(c.data["consultations"], c, NOW)
    assert len(summaries) == 1
    assert summaries[0]["free_count"] == 0
    c, _ = coordinator(hass, c.data, options={"consultations_my_teachers": False})
    assert len(block_summaries(c.data["consultations"], c, NOW)) == 2


async def test_teacher_attributes_and_next_block(hass):
    b = block(is_class_teacher=True)
    reservation = ConsultationReservation("b1", b.starts_at, "v1")
    item = consultation_item(reservation, {"b1": b})
    assert item["teacher_name"] == "Teacher t1"
    assert item["end"] == (b.starts_at + timedelta(minutes=30)).isoformat()
    assert item["is_class_teacher"]
    c, _ = coordinator(hass, data([b]))
    sensor = EduPageParticipationSensor(c, 1, "Student", "next_consultation_block")
    with patch(
        "custom_components.homeassistantedupage.sensor.dt_util.now", return_value=NOW
    ):
        assert sensor.state == b.starts_at
        assert sensor.extra_state_attributes["blocks"][0]["free_count"] == 0


def test_registration_flags_open_and_parent_confirmation():
    a = Registration(
        "a", "Action", active=True, votable=True, requires_parent_confirmation=True
    )
    s = registration_summary(a, "school", NOW)
    assert s["needs_action"] and s["open"]
    a.active = False
    assert not registration_summary(a, "school", NOW)["open"]


async def test_select_is_local_and_button_explicit(hass):
    b = block()
    c, e = coordinator(
        hass, data([b], [ConsultationSlot("b1", b.starts_at, "free", False)])
    )
    select = ConsultationSelect(c, e, False)
    select.async_write_ha_state = Mock()
    with patch(
        "custom_components.homeassistantedupage.select.dt_util.now", return_value=NOW
    ):
        option = select.options[0]
        await select.async_select_option(option)
        assert select.current_option == option
    c.edupage.consultation_action.assert_not_called()
    button = ConsultationButton(c, e, "book")
    button.hass = hass
    button.async_write_ha_state = Mock()
    with patch(
        "custom_components.homeassistantedupage.button.perform_action", new=AsyncMock()
    ) as perform:
        await button.async_press()
        assert perform.call_args.kwargs["dry_run"] is False
    assert c._selected_slot is None


async def test_calendar_own_reservations(hass):
    b = block()
    c, e = coordinator(
        hass, data([b], bookings=[ConsultationReservation("b1", b.starts_at, "v1")])
    )
    calendar = EduPageConsultationCalendar(c, e.data)
    events = await calendar.async_get_events(hass, NOW, NOW + timedelta(days=5))
    assert len(events) == 1 and events[0].uid == "v1"
    assert events[0].summary == "Teacher t1"
    assert events[0].end - events[0].start == timedelta(minutes=30)
    c.data["data_ok"]["consultations"] = False
    assert not calendar.available


async def test_service_default_dry_run_and_explicit_entry(hass):
    c, e = coordinator(hass)
    await async_setup_services(hass)
    result = await hass.services.async_call(
        "homeassistantedupage",
        "book_consultation",
        {
            "entry_id": e.entry_id,
            "consultation_id": "b1",
            "start": (NOW + timedelta(days=3)).isoformat(),
        },
        blocking=True,
        return_response=True,
    )
    assert result["dry_run"]
    assert c.edupage.consultation_action.call_args.kwargs["dry_run"] is True
    c.async_request_refresh.assert_not_called()
    with pytest.raises(ServiceValidationError):
        await perform_action(
            hass, "other", "book", consultation_id="b1", start=NOW.isoformat()
        )


async def test_service_booking_response_and_immediate_refresh(hass):
    b = block()
    c, e = coordinator(hass, data([b]))
    c.edupage.consultation_action.return_value = ConsultationReservation(
        "b1", b.starts_at, "v1"
    )
    result = await perform_action(
        hass,
        e.entry_id,
        "book",
        consultation_id="b1",
        start=b.starts_at.isoformat(),
        dry_run=False,
    )
    assert result["visit_id"] == "v1" and result["teacher_name"] == "Teacher t1"
    c.async_request_refresh.assert_awaited_once()


async def test_uncertain_write_is_not_retried(hass):
    c, e = coordinator(hass)
    c.edupage.consultation_action.side_effect = ConsultationUncertain()
    with pytest.raises(ServiceValidationError) as error:
        await perform_action(
            hass,
            e.entry_id,
            "book",
            consultation_id="b1",
            start=NOW.isoformat(),
            dry_run=False,
        )
    assert error.value.translation_key == "booking_uncertain"
    assert c.edupage.consultation_action.await_count == 1
    c.async_request_refresh.assert_awaited_once()


async def test_baseline_outage_and_external_cancellation(hass):
    b = block()
    r = ConsultationReservation("b1", b.starts_at, "v1")
    c, _ = coordinator(hass, data([b], bookings=[r]))
    tracker = ParticipationTracker()
    assert tracker.update(c, NOW) == []
    c.data = {"consultations": None, "data_ok": {"consultations": False}}
    assert tracker.update(c, NOW) == []
    c.data = data([b], bookings=[])
    events = tracker.update(c, NOW)
    assert [kind for kind, _ in events] == ["consultation_cancelled"]
    assert events[0][1]["source"] == "outside_home_assistant"
    assert tracker.update(c, NOW) == []


async def test_ha_cancellation_suppression_and_changed_booking(hass):
    b = block()
    r = ConsultationReservation("b1", b.starts_at, "v1")
    c, _ = coordinator(hass, data([b], bookings=[r]))
    tracker = ParticipationTracker()
    tracker.update(c, NOW)
    c.data = data(
        [b], bookings=[replace(r, starts_at=r.starts_at + timedelta(minutes=30))]
    )
    assert [k for k, _ in tracker.update(c, NOW)] == ["consultation_changed"]
    c._ha_cancelled_visits = {"v1"}
    c.data = data([b])
    assert tracker.update(c, NOW) == []


async def test_new_block_opening_and_deadline_once(hass):
    b = block()
    b.booking_opens_at = NOW + timedelta(hours=1)
    b.booking_closes_at = NOW + timedelta(hours=25)
    c, _ = coordinator(hass, data([b]))
    tracker = ParticipationTracker()
    assert tracker.update(c, NOW) == []
    events = tracker.update(c, NOW + timedelta(hours=1))
    assert {k for k, _ in events} == {
        "consultation_booking_opened",
        "consultation_booking_closing",
    }
    assert tracker.update(c, NOW + timedelta(hours=1)) == []
    c.data["consultations"]["overview"].consultations.append(block("new"))
    assert "consultation_block_published" in {
        k for k, _ in tracker.update(c, NOW + timedelta(hours=2))
    }


async def test_scheduler_exact_opening_and_unload(hass):
    b = block()
    b.booking_opens_at = NOW + timedelta(hours=1)
    c, e = coordinator(hass, data([b]))
    with (
        patch(
            "custom_components.homeassistantedupage.participation_monitor.dt_util.now",
            return_value=NOW,
        ),
        patch(
            "custom_components.homeassistantedupage.participation_monitor.async_track_point_in_utc_time",
            return_value=Mock(),
        ) as schedule,
    ):
        setup_refresh_schedule(c, e)
        assert schedule.call_args.args[2] == b.booking_opens_at
        assert c.update_interval == timedelta(minutes=30)
        await schedule.call_args.args[1](b.booking_opens_at)
        c.async_request_refresh.assert_awaited_once()
        e._on_unload[-1]()


@pytest.mark.parametrize("dry_run", [True, False])
async def test_teacher_filter_enforced_before_any_booking(dry_run):
    from custom_components.homeassistantedupage.homeassistant_edupage import Edupage
    from edupage_api.exceptions import ConsultationError
    from test_participation import FakeHass

    wrapper = Edupage(FakeHass())
    wrapper.api = Mock()
    wrapper.api.get_consultations.return_value = ConsultationOverview(
        [block(teaches=False)], []
    )
    wrapper.api.get_classes.return_value = []
    with pytest.raises(ConsultationError) as error:
        await wrapper.consultation_action(
            SimpleNamespace(person_id=2, class_id=9),
            "book_consultation_slot",
            consultation_id="b1",
            starts_at=NOW,
            dry_run=dry_run,
            require_related_teacher=True,
        )
    assert error.value.code == "unrelated_teacher"
    wrapper.api.book_consultation_slot.assert_not_called()
    wrapper.api.switch_to_parent.assert_called_once()


async def test_explicit_teacher_override_reaches_api():
    from custom_components.homeassistantedupage.homeassistant_edupage import Edupage
    from test_participation import FakeHass

    wrapper = Edupage(FakeHass())
    wrapper.api = Mock()
    await wrapper.consultation_action(
        SimpleNamespace(person_id=2),
        "book_consultation_slot",
        consultation_id="b1",
        starts_at=NOW,
        dry_run=True,
        require_related_teacher=False,
    )
    wrapper.api.book_consultation_slot.assert_called_once()
    assert (
        "require_related_teacher"
        not in wrapper.api.book_consultation_slot.call_args.kwargs
    )


async def test_service_passes_teacher_policy_and_override(hass):
    c, e = coordinator(hass)
    await perform_action(
        hass, e.entry_id, "book", consultation_id="b1", start=NOW.isoformat()
    )
    assert (
        c.edupage.consultation_action.call_args.kwargs["require_related_teacher"]
        is True
    )
    await perform_action(
        hass,
        e.entry_id,
        "book",
        consultation_id="b1",
        start=NOW.isoformat(),
        allow_unrelated_teacher=True,
    )
    assert (
        c.edupage.consultation_action.call_args.kwargs["require_related_teacher"]
        is False
    )


async def test_selector_labels_use_numeric_dates(hass):
    b = block()
    c, e = coordinator(
        hass, data([b], [ConsultationSlot("b1", b.starts_at, "free", False)])
    )
    select = ConsultationSelect(c, e, False)
    with patch(
        "custom_components.homeassistantedupage.select.dt_util.now", return_value=NOW
    ):
        assert "24. 10. 2026" in select.options[0]
        assert not any(day in select.options[0] for day in ("Tue", "Thu", "Sat"))


async def test_fresh_child_teacher_relation_is_accepted():
    from custom_components.homeassistantedupage.homeassistant_edupage import Edupage
    from test_participation import FakeHass

    wrapper = Edupage(FakeHass())
    wrapper.api = Mock()
    wrapper.api.get_consultations.return_value = ConsultationOverview(
        [block(teaches=True)], []
    )
    await wrapper.consultation_action(
        SimpleNamespace(person_id=2),
        "book_consultation_slot",
        consultation_id="b1",
        starts_at=NOW,
        dry_run=True,
        require_related_teacher=True,
    )
    wrapper.api.book_consultation_slot.assert_called_once()
    wrapper.api.switch_to_parent.assert_called_once()


async def test_overlapping_blocks_show_slot_and_block_ends(hass):
    a = block("2")
    b = replace(block("3"), slot_minutes=15, ends_at=a.ends_at + timedelta(hours=1))
    c, entry = coordinator(hass, data([a,b], [
        ConsultationSlot("2", a.starts_at, "free", False),
        ConsultationSlot("3", b.starts_at, "free", False),
    ]))
    with patch("custom_components.homeassistantedupage.select.dt_util.now", return_value=NOW):
        choices = ConsultationSelect(c, entry, False).options
    assert len(choices) == 2
    # Different visible durations/block ends even without the technical IDs.
    assert choices[0].split(" [")[0] != choices[1].split(" [")[0]
    assert "–" in choices[0].split(" – ")[1]
