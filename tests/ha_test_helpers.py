"""Shared test helpers from the Home Assistant pytest plugin in use."""

try:
    from pytest_homeassistant.common import MockConfigEntry, async_capture_events
except ModuleNotFoundError as exc:
    if exc.name != "pytest_homeassistant":
        raise
    from pytest_homeassistant_custom_component.common import (
        MockConfigEntry,
        async_capture_events,
    )

__all__ = ["MockConfigEntry", "async_capture_events"]
