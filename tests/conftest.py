"""pytest configuration for the Home Assistant custom component tests.

Uses pytest-homeassistant-custom-component, which provides the ``hass``
fixture and related helpers. The custom component's directory is added to
sys.path so it can be imported under ``custom_components.homeassistantedupage``.
"""

import os
import sys

# Make the custom_components package importable under pytest-homeassistant.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))