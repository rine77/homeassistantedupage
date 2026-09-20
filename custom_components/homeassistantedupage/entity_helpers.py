"""Shared helpers for EduPage entities."""

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN


def student_initials(student_name: str | None) -> str:
    """Return compact uppercase initials for a student's display name."""
    parts = [part for part in str(student_name or "").split() if part]
    if not parts:
        return "?"
    return "".join(part[0].upper() for part in parts)


def compact_entity_name(student_name: str | None, label: str) -> str:
    """Return a compact per-student entity name without an EduPage prefix."""
    return f"[{student_initials(student_name)}] {label}"


def student_device_info(student_id: Any, student_name: str | None) -> DeviceInfo:
    """Return the stable Home Assistant device definition for a student."""
    display_name = student_name or str(student_id)
    return DeviceInfo(
        identifiers={(DOMAIN, str(student_id))},
        name=f"EduPage - {display_name}",
        manufacturer="EduPage",
    )
