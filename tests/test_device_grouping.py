"""Tests for migrating EduPage entities to their student device."""

from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.homeassistantedupage import (
    _async_group_config_entry_entities,
)
from custom_components.homeassistantedupage.const import DOMAIN


def test_groups_existing_and_disabled_config_entry_entities(hass):
    """Registry entries are migrated even when no entity is currently loaded."""
    entry = MockConfigEntry(domain=DOMAIN, entry_id="student-entry")
    entry.add_to_hass(hass)
    other_entry = MockConfigEntry(domain=DOMAIN, entry_id="other-entry")
    other_entry.add_to_hass(hass)
    registry = er.async_get(hass)
    existing = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        "legacy-grade",
        config_entry=entry,
    )
    disabled = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        "unselected-subject",
        config_entry=entry,
        disabled_by=er.RegistryEntryDisabler.INTEGRATION,
    )
    unrelated = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        "other-student",
        config_entry=other_entry,
    )

    _async_group_config_entry_entities(hass, entry, 42, "Max Example")

    device_id = registry.async_get(existing.entity_id).device_id
    device = dr.async_get(hass).async_get(device_id)
    assert device is not None
    assert device.identifiers == {(DOMAIN, "42")}
    assert registry.async_get(existing.entity_id).device_id == device.id
    assert registry.async_get(disabled.entity_id).device_id == device.id
    assert registry.async_get(unrelated.entity_id).device_id is None


def test_grouping_is_idempotent(hass):
    """Reloading the integration keeps the same device association."""
    entry = MockConfigEntry(domain=DOMAIN, entry_id="student-entry")
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    existing = registry.async_get_or_create(
        "calendar",
        DOMAIN,
        "legacy-calendar",
        config_entry=entry,
    )

    _async_group_config_entry_entities(hass, entry, 42, "Max Example")
    first_device_id = registry.async_get(existing.entity_id).device_id
    _async_group_config_entry_entities(hass, entry, 42, "Max Example")

    assert registry.async_get(existing.entity_id).device_id == first_device_id
