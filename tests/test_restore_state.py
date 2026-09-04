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

from custom_components.homeassistantedupage.const import (
    CONF_STUDENT_ID,
    CONF_STUDENT_NAME,
    DOMAIN,
)
from custom_components.homeassistantedupage.sensor import (
    async_setup_entry,
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
    def __init__(self, grade_n, subject_name="Maths", subject_id=1):
        self.grade_n = grade_n
        self.subject_name = subject_name
        self.subject_id = subject_id


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


def _subject_sensor(coord, grades=None):
    coord.data["grades"] = grades if grades is not None else []
    return EduPageSubjectSensor(coord, 1, "Max Kov", "Maths", 1, grades or [])


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
    # A successful update yields a different value (read live from coordinator).
    coord.data["grades"] = [_Grade(1), _Grade(2)]
    assert sensor.state == 2
    # Now an outage: the restored value 5 must NOT come back; 2 is current.
    _outage(coord)
    assert sensor.state == 2


async def test_later_successful_refresh_updates_subject_grades(hass, coord):
    """The subject sensor must observe a later successful refresh that replaces
    coordinator grade data with new grades (no private attribute touch)."""
    sensor = _subject_sensor(coord, [_Grade(1, subject_id=1)])
    _set_data_ok(coord, grades=True)
    assert sensor.state == 1

    # New grades arrive on a later successful poll, including another subject
    # that this sensor must ignore.
    coord.data["grades"] = [
        _Grade(2, subject_name="Maths", subject_id=1),
        _Grade(3, subject_name="Maths", subject_id=1),
        _Grade(4, subject_name="English", subject_id=1),
        _Grade(9, subject_name="Other", subject_id=99),
    ]
    _set_data_ok(coord, grades=True)
    assert sensor.state == 3

    # The per-subject attribute list also tracks the live data.
    assert len(sensor._current_grades) == 3



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


# ---------------------------------------------------------------------------
# Non-restorable states (unknown / unavailable / invalid) are ignored
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_state", ["unknown", "unavailable", "none", "abc", "not-a-number"]
)
async def test_unknown_unavailable_are_not_restored_as_real_values(
    hass, coord, bad_state
):
    """Restoring 'unknown'/'unavailable'/invalid must not produce a value."""
    sensor = _subject_sensor(coord)
    sensor._apply_restored(_FakeState(bad_state))
    _outage(coord)
    # _last_value stays None -> not treated as available, no invented 0.
    assert sensor._last_value is None
    assert sensor.available is False


async def test_unknown_restored_does_not_fake_grade_count(hass, coord):
    """An 'unknown' grade count must not appear as a genuine zero."""
    sensor = _subject_sensor(coord)
    sensor._apply_restored(_FakeState("unknown"))
    assert sensor._last_value is None
    # Without fresh data and without a seeded value the sensor is unavailable.
    _outage(coord)
    assert sensor.available is False


async def test_unknown_restored_for_term_average(hass, coord):
    sensor = _term_sensor(coord)
    sensor._apply_restored(_FakeState("unknown"))
    _outage(coord)
    assert sensor._last_value is None
    assert sensor.available is False


async def test_empty_string_not_restored(hass, coord):
    sensor = _notification_sensor(coord)
    sensor._apply_restored(_FakeState(""))
    _outage(coord)
    assert sensor._last_value is None
    assert sensor.available is False


# ---------------------------------------------------------------------------
# Partial coordinator failures: per-section freshness (#109 integration)
# ---------------------------------------------------------------------------


def _set_data_ok(coord, **sections):
    """Seed the per-section freshness map on a coordinator."""
    coord.data["data_ok"] = coord.data.get("data_ok", {}) or {}
    coord.data["data_ok"].update(sections)


async def test_grade_section_failure_keeps_last_value(hass, coord):
    """When grades fail (#109) but the rest of the poll succeeds, the subject
    sensor must not treat its data as fresh or reset to 0."""
    sensor = _subject_sensor(coord, [_Grade(1), _Grade(2), _Grade(3)])
    # A good poll: grades fresh -> count 3 is remembered.
    _set_data_ok(coord, grades=True)
    assert sensor.state == 3
    # Next poll: grades fail -> coordinator data is empty AND data_ok False,
    # with last_update_success still True (partial failure).
    coord.data["grades"] = []
    _set_data_ok(coord, grades=False)
    # Not fresh: keep the last-known 3, marked stale, not reset to 0.
    assert sensor.state == 3
    assert sensor.data_stale is True


async def test_grade_failure_marks_subject_stale(hass, coord):
    sensor = _subject_sensor(coord)
    sensor._apply_restored(_FakeState("4"))
    _set_data_ok(coord, grades=False)
    # Whole coordinator is up but the grades section failed -> stale grade data.
    assert sensor.data_stale is True
    assert sensor.state == 4


async def test_section_failure_makes_available_false_without_last_value(hass, coord):
    """The overall coordinator update succeeds but this sensor's own section
    failed and there is no restored/last-known value to fall back on: the sensor
    must be unavailable (not report an invented 0 as available)."""
    sensor = _subject_sensor(coord)
    # No restored / last-known value.
    assert sensor._last_value is None
    # Coordinator globally up, but this sensor's section (grades) failed.
    coord.last_update_success = True
    _set_data_ok(coord, grades=False)
    assert sensor.available is False



async def test_notification_failure_independent_of_grades(hass, coord):
    """Grades may fail while notifications stay fresh; each sensor is independent."""
    grade_sensor = _subject_sensor(coord)
    notif_sensor = _notification_sensor(coord)

    coord.data["grades"] = [_Grade(1)]
    coord.data["notifications"] = [1, 2, 3]
    _set_data_ok(coord, grades=False, notifications=True)

    # Notifications fresh -> count 3, not stale.
    assert notif_sensor.state == 3
    assert notif_sensor.data_stale is False
    # Grades failed -> not fresh, falls back to last-known (none here -> 0 path),
    # but is marked stale.
    assert grade_sensor.data_stale is True
    assert grade_sensor.state == 0


async def test_full_outage_still_makes_everything_stale(hass, coord):
    """A complete coordinator outage keeps the whole-data fallback (no data_ok
    needed) because last_update_success is False."""
    sensor = _notification_sensor(coord)
    assert sensor.state == 0
    _set_data_ok(coord, notifications=True)
    _outage(coord)
    assert sensor.data_stale is True
    assert sensor.available is True  # last-known value of 0 retained


async def test_setup_uses_stored_student_when_first_refresh_fails(hass, coord):
    """When EduPage is unavailable at startup, sensor setup must fall back to the
    stored student id/name from the config entry so sensor construction does not
    crash with a None name (which would skip RestoreEntity attachment)."""
    from homeassistant.config_entries import ConfigEntry

    # First refresh produced no student data at all.
    coord.data = {}
    coord.last_update_success = False

    entry = ConfigEntry(
        version=1,
        minor_version=1,
        domain=DOMAIN,
        title="Edupage (Max)",
        data={
            "username": "u",
            "subdomain": "s",
            "phpsessid": "p",
            CONF_STUDENT_ID: 42,
            CONF_STUDENT_NAME: "Max Kov",
        },
        source="user",
        entry_id="entry-fallback",
        unique_id="unique-fallback",
        discovery_keys=set(),
        options={},
    )
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coord

    added = []

    def _add_entities(entities, update_before_add=False):
        added.extend(entities)

    await async_setup_entry(hass, entry, _add_entities)

    assert added, "expected at least the notification sensor to be created"
    # Construction succeeded (no unidecode(None)); the stored name survives.
    notif = added[0]
    assert notif._student_id == 42
    assert notif._student_name == "max_kov"
