"""Regression tests for compact per-student entity names."""

from pathlib import Path


PLATFORM_FILES = (
    Path("custom_components/homeassistantedupage/sensor.py"),
    Path("custom_components/homeassistantedupage/calendar.py"),
    Path("custom_components/homeassistantedupage/event.py"),
    Path("custom_components/homeassistantedupage/todo.py"),
)


def test_all_entity_platforms_use_shared_compact_name_helper():
    """Every entity platform uses the same per-student naming scheme."""
    for path in PLATFORM_FILES:
        source = path.read_text(encoding="utf-8")
        assert "compact_entity_name" in source, path


def test_default_entity_names_do_not_repeat_edupage_prefix():
    """Entity names should not repeat the integration or device name."""
    for path in PLATFORM_FILES:
        source = path.read_text(encoding="utf-8")
        assert 'f"EduPage - ' not in source, path
        assert 'f"Edupage - ' not in source, path
