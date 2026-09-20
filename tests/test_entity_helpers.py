"""Tests for shared EduPage entity helpers."""

from custom_components.homeassistantedupage.const import DOMAIN
from custom_components.homeassistantedupage.entity_helpers import (
    compact_entity_name,
    student_initials,
    student_device_info,
)


def test_student_initials_use_each_name_part():
    """Initials distinguish students without repeating their full names."""
    assert student_initials("Max Maria Example") == "MME"
    assert student_initials("  Max   Example  ") == "ME"


def test_student_initials_handle_missing_name():
    """Missing names still produce a usable entity name."""
    assert student_initials(None) == "?"
    assert student_initials("") == "?"


def test_compact_entity_name_combines_initials_and_label():
    """Compact names consistently identify both student and entity purpose."""
    assert compact_entity_name("Max Example", "Homework") == "[ME] Homework"


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
