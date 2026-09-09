"""Tests for configurable per-subject grade sensors."""

import logging
from types import SimpleNamespace

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from custom_components.homeassistantedupage.const import DOMAIN
from custom_components.homeassistantedupage.sensor import (
    EduPageSubjectSensor,
    async_setup_entry,
)


class _FakeSubject:
    def __init__(self, subject_id, name):
        self.subject_id = subject_id
        self.name = name


@pytest.fixture
def coordinator(hass: HomeAssistant):
    """Coordinator containing two school-wide subjects."""
    coord = DataUpdateCoordinator(
        hass,
        logging.getLogger("test"),
        name="test",
        config_entry=None,
    )
    coord.data = {
        "student": {"id": 1, "name": "Max"},
        "subjects": [
            _FakeSubject(1, "Maths"),
            _FakeSubject(2, "English"),
        ],
        "grades": [],
        "notifications": [],
        "timetable_changes": [],
        "missing_teachers": [],
        "next_ringing": None,
        "grades_per_term": {"first": [], "second": []},
    }
    return coord


async def _setup_sensors(hass, coordinator, options):
    entry = SimpleNamespace(
        entry_id="subject-selection-test",
        data={},
        options=options,
    )
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    added_entities = []

    def add_entities(entities, update_before_add=False):
        added_entities.extend(entities)

    await async_setup_entry(hass, entry, add_entities)

    return [
        entity
        for entity in added_entities
        if isinstance(entity, EduPageSubjectSensor)
    ]


async def test_missing_option_preserves_all_subject_sensors(hass, coordinator):
    """Existing entries without the option retain their current behavior."""
    sensors = await _setup_sensors(hass, coordinator, options={})

    assert {sensor._subject_id for sensor in sensors} == {1, 2}


async def test_only_selected_subject_sensors_are_created(hass, coordinator):
    """Only explicitly selected subjects create individual grade sensors."""
    sensors = await _setup_sensors(
        hass,
        coordinator,
        options={"subject_ids": ["2"]},
    )

    assert {sensor._subject_id for sensor in sensors} == {2}


async def test_empty_selection_creates_no_subject_sensors(hass, coordinator):
    """An empty selection disables all individual grade sensors."""
    sensors = await _setup_sensors(
        hass,
        coordinator,
        options={"subject_ids": []},
    )

    assert sensors == []
