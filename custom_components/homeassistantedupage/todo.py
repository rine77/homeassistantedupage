"""Read-only EduPage homework to-do entities."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from homeassistant.components.todo import TodoItem, TodoItemStatus, TodoListEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .assignment_helpers import event_matches_student
from .const import CONF_STUDENT_ID, CONF_STUDENT_NAME, DOMAIN
from .entity_helpers import student_device_info
from .event import _event_type_value

_HOMEWORK_EVENT_TYPE = "homework"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up a read-only homework list for the configured student."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    student = coordinator.data.get("student", {}) if coordinator.data else {}
    student_id = student.get("id", entry.data.get(CONF_STUDENT_ID))
    student_name = student.get("name") or entry.data.get(CONF_STUDENT_NAME)

    async_add_entities(
        [EduPageHomeworkTodoEntity(coordinator, student_id, student_name)]
    )


def _parse_due(value: Any) -> date | None:
    """Parse the date portion of an EduPage homework deadline."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except (TypeError, ValueError):
        return None


class EduPageHomeworkTodoEntity(CoordinatorEntity, TodoListEntity):
    """Represent EduPage homework as a read-only Home Assistant to-do list."""

    _attr_supported_features = 0

    def __init__(self, coordinator, student_id, student_name) -> None:
        """Initialize the homework list."""
        super().__init__(coordinator)
        self._student_id = student_id
        self._student_name = student_name or str(student_id)
        self._attr_name = f"EduPage - Homework {self._student_name}"
        self._attr_unique_id = f"edupage_homework_{self._student_id}"
        self._attr_device_info = student_device_info(
            self._student_id, self._student_name
        )

    def _subject_name(self, subject_id: Any) -> str | None:
        """Resolve a subject ID through the coordinator's subject list."""
        if subject_id is None or not self.coordinator.data:
            return None
        for subject in self.coordinator.data.get("subjects", []) or []:
            if str(getattr(subject, "subject_id", "")) == str(subject_id):
                return getattr(subject, "name", None)
        return None

    def _map_item(self, event: Any) -> TodoItem:
        """Map one EduPage homework event to a Home Assistant to-do item."""
        additional_data = getattr(event, "additional_data", None) or {}
        subject = self._subject_name(additional_data.get("predmetid"))
        text = str(getattr(event, "text", None) or "Homework")
        author = getattr(event, "author", None)
        author_name = getattr(author, "name", None) or author

        description_parts = []
        if subject:
            description_parts.append(f"Subject: {subject}")
        if author_name:
            description_parts.append(f"Author: {author_name}")

        is_done = bool(getattr(event, "is_done", False))
        return TodoItem(
            uid=str(getattr(event, "event_id")),
            summary=f"{subject}: {text}" if subject else text,
            status=(
                TodoItemStatus.COMPLETED
                if is_done
                else TodoItemStatus.NEEDS_ACTION
            ),
            due=_parse_due(additional_data.get("date")),
            description="\n".join(description_parts) or None,
        )

    @property
    def todo_items(self) -> list[TodoItem]:
        """Return homework items from the latest coordinator data."""
        if not self.coordinator.data:
            return []
        notifications = self.coordinator.data.get("notifications", []) or []
        items = [
            self._map_item(event)
            for event in notifications
            if _event_type_value(event) == _HOMEWORK_EVENT_TYPE
            and getattr(event, "event_id", None) is not None
            and event_matches_student(
                event, self._student_id, self._student_name
            )
        ]
        return sorted(
            items,
            key=lambda item: (
                item.status == TodoItemStatus.COMPLETED,
                item.due is None,
                item.due or date.max,
                item.summary or "",
            ),
        )
