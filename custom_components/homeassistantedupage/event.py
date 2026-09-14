"""EduPage event entities."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from homeassistant.components.event import EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_STUDENT_ID, CONF_STUDENT_NAME, DOMAIN

EVENT_NEW_GRADE = "new_grade"
EVENT_NEW_HOMEWORK = "new_homework"
EVENT_NEW_MESSAGE = "new_message"
EVENT_NEW_EXAM = "new_exam"
EVENT_TIMETABLE_CHANGE = "timetable_change"
EVENT_ARRIVAL_AT_SCHOOL = "arrival_at_school"
EVENT_EDUPAGE = f"{DOMAIN}_event"

EVENT_TYPES = [
    EVENT_NEW_GRADE,
    EVENT_NEW_HOMEWORK,
    EVENT_NEW_MESSAGE,
    EVENT_NEW_EXAM,
    EVENT_TIMETABLE_CHANGE,
    EVENT_ARRIVAL_AT_SCHOOL,
]

_TYPE_MAP = {
    "znamka": EVENT_NEW_GRADE,
    "homework": EVENT_NEW_HOMEWORK,
    "sprava": EVENT_NEW_MESSAGE,
    "bexam": EVENT_NEW_EXAM,
    "oexam": EVENT_NEW_EXAM,
    "rexam": EVENT_NEW_EXAM,
    "pexam": EVENT_NEW_EXAM,
    "sexam": EVENT_NEW_EXAM,
    "testing": EVENT_NEW_EXAM,
    "testpridelenie": EVENT_NEW_EXAM,
    "substitution": EVENT_TIMETABLE_CHANGE,
    "changeroom": EVENT_TIMETABLE_CHANGE,
    "bookroom": EVENT_TIMETABLE_CHANGE,
    "timetable": EVENT_TIMETABLE_CHANGE,
    "pipnutie": EVENT_ARRIVAL_AT_SCHOOL,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the EduPage event entity."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    student = coordinator.data.get("student", {}) if coordinator.data else {}
    student_id = student.get("id", entry.data.get(CONF_STUDENT_ID))
    student_name = student.get("name") or entry.data.get(CONF_STUDENT_NAME)

    async_add_entities(
        [EduPageEventEntity(coordinator, student_id, student_name)]
    )


def _event_type_value(event: Any) -> str:
    """Return the raw EduPage event type value."""
    event_type = getattr(event, "event_type", None)
    return str(getattr(event_type, "value", event_type) or "")


def _serialize_value(value: Any) -> Any:
    """Convert common EduPage values to Home Assistant-safe values."""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _serialize_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialize_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


class EduPageEventEntity(CoordinatorEntity, EventEntity):
    """Emit Home Assistant events for new EduPage timeline entries."""

    _attr_event_types = EVENT_TYPES

    def __init__(self, coordinator, student_id, student_name) -> None:
        """Initialize the event entity and baseline known notification IDs."""
        super().__init__(coordinator)
        self._student_id = student_id
        self._student_name = student_name or str(student_id)
        self._attr_name = f"EduPage - Events {self._student_name}"
        self._attr_unique_id = f"edupage_events_{self._student_id}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(self._student_id))},
            name=f"EduPage - {self._student_name}",
            manufacturer="EduPage",
        )
        self._known_event_ids = self._notification_ids()

    def _device_id(self) -> str | None:
        """Return the Home Assistant device ID for this student."""
        device = dr.async_get(self.coordinator.hass).async_get_device(
            identifiers={(DOMAIN, str(self._student_id))}
        )
        return device.id if device is not None else None

    def _notifications(self) -> list[Any]:
        """Return current timeline notifications."""
        if not self.coordinator.data:
            return []
        return self.coordinator.data.get("notifications", []) or []

    def _notification_ids(self) -> set[int]:
        """Return valid event IDs from the current coordinator data."""
        return {
            event_id
            for event in self._notifications()
            if (event_id := getattr(event, "event_id", None)) is not None
        }

    def _subject_name(self, subject_id: Any) -> str | None:
        """Resolve a subject ID through the coordinator's subject list."""
        if subject_id is None or not self.coordinator.data:
            return None
        for subject in self.coordinator.data.get("subjects", []) or []:
            if str(getattr(subject, "subject_id", "")) == str(subject_id):
                return getattr(subject, "name", None)
        return None

    def _event_attributes(self, event: Any, raw_type: str) -> dict[str, Any]:
        """Map an EduPage timeline event to HA event attributes."""
        additional_data = getattr(event, "additional_data", None) or {}
        author = getattr(event, "author", None)
        author_name = getattr(author, "name", None) or author
        subject_id = additional_data.get("predmetid")

        attributes = {
            "event_id": getattr(event, "event_id", None),
            "edupage_event_type": raw_type,
            "student_id": self._student_id,
            "student_name": self._student_name,
            "text": getattr(event, "text", None),
            "timestamp": getattr(event, "timestamp", None),
            "author": author_name,
            "subject": self._subject_name(subject_id),
            "subject_id": subject_id,
            "deadline": additional_data.get("date"),
            "is_done": getattr(event, "is_done", False),
            "is_starred": getattr(event, "is_starred", False),
            "additional_data": additional_data,
        }
        return {
            key: _serialize_value(value)
            for key, value in attributes.items()
            if value is not None
        }

    def _handle_coordinator_update(self) -> None:
        """Emit supported notifications that appeared since the last update."""
        notifications = self._notifications()
        current_ids = self._notification_ids()
        event_triggered = False

        # EduPage returns the newest notifications first. Trigger older new
        # entries first so multiple events from one poll remain chronological.
        for event in reversed(notifications):
            event_id = getattr(event, "event_id", None)
            if event_id is None or event_id in self._known_event_ids:
                continue
            raw_type = _event_type_value(event)
            mapped_type = _TYPE_MAP.get(raw_type)
            if mapped_type is not None:
                attributes = self._event_attributes(event, raw_type)
                self._trigger_event(
                    mapped_type,
                    attributes,
                )
                self.coordinator.hass.bus.async_fire(
                    EVENT_EDUPAGE,
                    {
                        "device_id": self._device_id(),
                        "type": mapped_type,
                        **attributes,
                    },
                )
                self.async_write_ha_state()
                event_triggered = True

        self._known_event_ids.update(current_ids)
        if not event_triggered:
            self.async_write_ha_state()
