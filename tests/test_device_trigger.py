"""Tests for EduPage device triggers."""

from unittest.mock import AsyncMock

from homeassistant.const import CONF_DEVICE_ID, CONF_DOMAIN, CONF_PLATFORM, CONF_TYPE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.homeassistantedupage.const import DOMAIN
from custom_components.homeassistantedupage.device_trigger import (
    async_attach_trigger,
    async_get_triggers,
)
from custom_components.homeassistantedupage.event import (
    EVENT_EDUPAGE,
    EVENT_NEW_GRADE,
    EVENT_TYPES,
)


def _create_device(hass: HomeAssistant):
    """Create an EduPage device in the registry."""
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    return dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "1")},
        name="EduPage - Max Example",
    )


async def test_get_triggers_lists_all_supported_types(hass: HomeAssistant):
    """Every event type is exposed in the visual automation editor."""
    device = _create_device(hass)

    triggers = await async_get_triggers(hass, device.id)

    assert {trigger[CONF_TYPE] for trigger in triggers} == set(EVENT_TYPES)
    assert all(trigger[CONF_PLATFORM] == "device" for trigger in triggers)
    assert all(trigger[CONF_DOMAIN] == DOMAIN for trigger in triggers)
    assert all(trigger[CONF_DEVICE_ID] == device.id for trigger in triggers)


async def test_get_triggers_ignores_unrelated_device(hass: HomeAssistant):
    """Triggers are not offered for devices from another integration."""
    entry = MockConfigEntry(domain="other_domain")
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={("other_domain", "1")},
    )

    assert await async_get_triggers(hass, device.id) == []


async def test_attach_trigger_filters_device_and_type(hass: HomeAssistant):
    """The trigger fires only for its configured device and event type."""
    device = _create_device(hass)
    action = AsyncMock()
    config = {
        CONF_PLATFORM: "device",
        CONF_DOMAIN: DOMAIN,
        CONF_DEVICE_ID: device.id,
        CONF_TYPE: EVENT_NEW_GRADE,
    }

    remove = await async_attach_trigger(
        hass,
        config,
        action,
        {"name": "test", "trigger_data": {}, "variables": {}},
    )

    hass.bus.async_fire(
        EVENT_EDUPAGE,
        {"device_id": device.id, "type": "new_homework"},
    )
    hass.bus.async_fire(
        EVENT_EDUPAGE,
        {"device_id": "another-device", "type": EVENT_NEW_GRADE},
    )
    await hass.async_block_till_done()
    action.assert_not_awaited()

    hass.bus.async_fire(
        EVENT_EDUPAGE,
        {
            "device_id": device.id,
            "type": EVENT_NEW_GRADE,
            "subject": "Maths",
        },
    )
    await hass.async_block_till_done()

    action.assert_awaited_once()
    remove()
