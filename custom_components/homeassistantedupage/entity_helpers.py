"""Shared helpers for EduPage entities."""

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN


def student_device_info(student_id: Any, student_name: str | None) -> DeviceInfo:
    """Return the stable Home Assistant device definition for a student."""
    display_name = student_name or str(student_id)
    return DeviceInfo(
        identifiers={(DOMAIN, str(student_id))},
        name=f"EduPage - {display_name}",
        manufacturer="EduPage",
    )
