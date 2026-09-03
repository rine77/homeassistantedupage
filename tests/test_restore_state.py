"""Tests for the RestoreEntity-based state persistence added to the sensors.

These cover the semantics of ``StateRestoringSensor``:

* restoring a value after startup;
* startup without a previously stored state;
* replacing the restored value after a successful update;
* an outage after a successful update;
* the ``available`` behavior during an outage;
* preservation of the appropriate state types (counts int, averages float,
  ringing times string).

The ``hass`` fixture and a live ``DataUpdateCoordinator`` are used, mirroring
``test_sensor.py``. The restore seed is applied through ``_apply_restored``
(which ``async_added_to_hass`` delegates to) so the tests exercise the exact
coercion/restore logic without needing recorder/platform plumbing.
"""

import logging
from datetime import datetime, timedelta

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from custom_components.homeassistantedupage.sensor import (
    EduPageNotificationSensor,
    EduPageRingingSensor,
    EduPageSubjectSensor,
    EduPageSubstitutionSensor,
    EduPageTermAverageSensor,
)


class _FakeState:
    """A minimal recorder-restored State (only ``.state`` is read)."""

    def __init__(self, state):
        self.state = state


class _Grade:
    def __init__(self, grade_n, subject_name="Maths"):
        self.grade_n = grade_n
        self.subject_name = subject_name


class _Ringing:
    def __init__(self, time):
        self.time = time

    class _Type:
        name = "bell"

    type = _Type()


@pytest.fixture
def coord(hass: HomeAssistant):
    """A coordinator whose data and freshness we control directly."""
    c = DataUpdateCoordinator(hass, logging.getLogger("test"), name="test")
    c.data = {
        "student": {"id": 1, "name": "Max"},
        "subjects": [],
        "notifications": [],
        "timetable_changes": [],
        "missing_teachers": [],
        "next_ringing": None,
        "grades_per_term": {"first": [], "second": []},
    }
    c.last_update_success = True
    return c


def _subject_sensor(coord):
    return EduPageSubjectSensor(coord, 1, "Max Kov", "Maths", [])


def _notification_sensor(coord):
    return EduPageNotificationSensor(coord, 1, "Max Kov", [])


def _substitution_sensor(coord):
    return EduPageSubstitutionSensor(coord, 1, "Max Kov", "timetable_changes")


def _ringing_sensor(coord):
    return EduPageRingingSensor(coord, 1, "Max Kov")


def _term_sensor(coord, term_key="first"):
    return EduPageTermAverageSensor(coord, 1, "Max Kov", term_key)


def _outage(coord):
    coord.last_update_success = False


def _recover(coord):
    coord.last_update_success = True


# ---------------------------------------------------------------------------
# Restore after startup
# ---------------------------------------------------------------------------


async def test_restores_value_after_startup(hass, coord):
    sensor = _subject_sensor(coord)
    sensor._apply_restored(_FakeState("4"))
    # No fresh data yet (coordinator refresh not completed).
    _outage(coord)
    assert sensor.state == 4
    assert isinstance(sensor.state, int)


async def test_outage_before_any_value_returns_zero(hass, coord):
    sensor = _subject_sensor(coord)
    _outage(coord)
    # No restored state and no fresh data yet.
    assert sensor.state == 0


async def test_restores_after_outage_with_tolerated_empty_data(hass, coord):
    sensor = _substitution_sensor(coord)
    sensor._apply_restored(_FakeState("3"))
    _outage(coord)
    assert sensor.state == 3
    assert isinstance(sensor.state, int)


# ---------------------------------------------------------------------------
# Replacing the restored value after a successful update
# ---------------------------------------------------------------------------


async def test_fresh_value_replaces_restored_value(hass, coord):
    sensor = _subject_sensor(coord)
    sensor._apply_restored(_FakeState("5"))
    # A successful update yields a different value.
    coord.data["grades"] = [_Grade(1), _Grade(2)]
    sensor._grades = coord.data["grades"]
    assert sensor.state == 2
    # Now an outage: the restored value 5 must NOT come back; 2 is current.
    _outage(coord)
    assert sensor.state == 2


# ---------------------------------------------------------------------------
# Outage after a successful update
# ---------------------------------------------------------------------------


async def test_outage_keeps_last_known_value(hass, coord):
    sensor = _notification_sensor(coord)
    assert sensor.state == 0
    coord.data["notifications"] = [1, 2, 3]
    assert sensor.state == 3
    _outage(coord)
    assert sensor.state == 3


async def test_outage_after_recovery_from_stale(hass, coord):
    sensor = _substitution_sensor(coord)
    sensor._apply_restored(_FakeState("3"))
    _outage(coord)
    assert sensor.state == 3
    # Recovery brings a real value that becomes the new last-known.
    _recover(coord)
    assert sensor.state == 0
    assert sensor.state == 0
    _outage(coord)
    assert sensor.state == 0


# ---------------------------------------------------------------------------
# Availability during an outage
# ---------------------------------------------------------------------------


async def test_available_while_holding_last_known_value(hass, coord):
    sensor = _notification_sensor(coord)
    assert sensor.state == 0
    _outage(coord)
    # Keeps holding the last-known value -> stays available and marked stale.
    assert sensor.available is True
    assert sensor.data_stale is True


async def test_data_stale_attribute_when_fresh(hass, coord):
    sensor = _substitution_sensor(coord)
    _recover(coord)
    assert sensor.data_stale is False
    attrs = sensor.extra_state_attributes
    assert attrs["data_stale"] is False
    _outage(coord)
    assert sensor.data_stale is True
    assert sensor.extra_state_attributes["data_stale"] is True


async def test_unavailable_with_no_value_and_no_fresh_data(hass, coord):
    sensor = _subject_sensor(coord)
    _outage(coord)
    # Never had a value and no fresh data -> coordinator availability governs.
    assert sensor.available is False


# ---------------------------------------------------------------------------
# State type preservation
# ---------------------------------------------------------------------------


async def test_subject_and_notification_counts_are_ints(hass, coord):
    subject = _subject_sensor(coord)
    subject._apply_restored(_FakeState("3"))
    _outage(coord)
    assert isinstance(subject.state, int)

    notif = _notification_sensor(coord)
    notif._apply_restored(_FakeState("7"))
    _outage(coord)
    assert isinstance(notif.state, int)


async def test_ringing_time_is_string(hass, coord):
    sensor = _ringing_sensor(coord)
    coord.data["next_ringing"] = _Ringing(datetime(2026, 9, 3, 8, 15))
    assert sensor.state == "08:15"
    assert isinstance(sensor.state, str)
    _outage(coord)
    assert sensor.state == "08:15"
    assert isinstance(sensor.state, str)


async def test_ringing_no_ring_is_unknown(hass, coord):
    sensor = _ringing_sensor(coord)
    coord.data["next_ringing"] = None
    assert sensor.state == "unknown"
    _outage(coord)
    assert sensor.state == "unknown"


async def test_term_average_is_float(hass, coord):
    sensor = _term_sensor(coord)
    coord.data["grades_per_term"]["first"] = [_Grade(1), _Grade(2)]
    value = sensor.state
    assert isinstance(value, float)
    _outage(coord)
    assert sensor.state == value


async def test_term_average_restored_as_float(hass, coord):
    sensor = _term_sensor(coord, term_key="second")
    sensor._apply_restored(_FakeState("2.5"))
    _outage(coord)
    assert sensor.state == 2.5
    assert isinstance(sensor.state, float)