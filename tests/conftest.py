"""pytest configuration for the Home Assistant custom component tests.

Uses pytest-homeassistant-custom-component, which provides the ``hass``
fixture and related helpers. The custom component's directory is added to
sys.path so it can be imported under ``custom_components.homeassistantedupage``.
"""

import os
import sys

import pytest

# Make the custom_components package importable under pytest-homeassistant.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture(autouse=True)
def _enable_custom_integrations(enable_custom_integrations):
    """Ensure custom integrations are registered with the HA loader.

    Depends on the ``enable_custom_integrations`` fixture provided by
    pytest-homeassistant-custom-component so ``flow.async_init`` can resolve the
    ``homeassistantedupage`` integration instead of raising UnknownHandler.
    """
    yield