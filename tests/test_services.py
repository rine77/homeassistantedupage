"""Tests for the global EduPage service registration and its coordinator
resolution.

Services are registered once, globally, and target a coordinator chosen via the
optional ``entry_id``. ``_resolve_coordinator`` must pick a specific entry when
``entry_id`` is given, reject ambiguity when several entries exist and none is
named, and return the single entry when only one is loaded.
"""

from unittest.mock import MagicMock

import pytest
import voluptuous as vol
from homeassistant.core import HomeAssistant

import custom_components.homeassistantedupage as init_module
from custom_components.homeassistantedupage.const import DOMAIN


def _call(data):
    call = MagicMock()
    call.data = data
    return call


def test_resolve_single_entry_without_id(hass: HomeAssistant):
    coord = object()
    hass.data[DOMAIN] = {"a": coord}
    assert init_module._resolve_coordinator(hass, _call({})) is coord


def test_resolve_by_entry_id(hass: HomeAssistant):
    coord_a = object()
    coord_b = object()
    hass.data[DOMAIN] = {"a": coord_a, "b": coord_b}
    assert init_module._resolve_coordinator(hass, _call({"entry_id": "b"})) is coord_b


def test_resolve_unknown_entry_id_raises(hass: HomeAssistant):
    """An explicitly supplied entry_id that is not loaded must raise, not fall
    back to the only loaded entry (acting on the wrong account is risky)."""
    coord_a = object()
    hass.data[DOMAIN] = {"a": coord_a}
    with pytest.raises(vol.Invalid):
        init_module._resolve_coordinator(hass, _call({"entry_id": "does-not-exist"}))


def test_resolve_missing_entry_id_raises_when_ambiguous(hass: HomeAssistant):
    hass.data[DOMAIN] = {"a": object(), "b": object()}
    with pytest.raises(vol.Invalid):
        init_module._resolve_coordinator(hass, _call({}))


def test_resolve_with_no_entries_raises(hass: HomeAssistant):
    hass.data[DOMAIN] = {}
    with pytest.raises(vol.Invalid):
        init_module._resolve_coordinator(hass, _call({}))
