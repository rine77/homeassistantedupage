"""Tests for shared EduPage entity helpers."""

from custom_components.homeassistantedupage.const import DOMAIN
from custom_components.homeassistantedupage.entity_helpers import (
    student_device_info,
)


def test_student_device_info_uses_stable_identifier():
    """Every platform receives the same per-student device definition."""
    device_info = student_device_info(42, "Max Example")

    assert device_info["identifiers"] == {(DOMAIN, "42")}
    assert device_info["name"] == "EduPage - Max Example"
    assert device_info["manufacturer"] == "EduPage"


def test_student_device_info_handles_missing_name():
    """Device construction remains safe during partial startup data."""
    device_info = student_device_info(42, None)

    assert device_info["name"] == "EduPage - 42"
