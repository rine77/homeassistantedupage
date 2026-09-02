import logging
from collections import defaultdict

from unidecode import unidecode
from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN

_LOGGER = logging.getLogger("custom_components.homeassistant_edupage")

# Bound on the number of events exposed in the "events" state attribute, to
# keep stored state attributes from growing without limit over time.
_MAX_EVENTS = 50


def group_grades_by_subject(grades):
    """grouping grades based on subject_id."""
    grouped = defaultdict(list)
    for grade in grades:
        grouped[grade.subject_id].append(grade)
    return grouped


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up EduPage sensors for each student and their grades."""
    _LOGGER.debug("SENSOR called async_setup_entry")
    coordinator = hass.data[DOMAIN][entry.entry_id]

    student = coordinator.data.get("student", {})
    subjects = coordinator.data.get("subjects", [])
    grades = coordinator.data.get("grades", [])
    notifications = coordinator.data.get("notifications", [])
    grades_by_subject = group_grades_by_subject(grades)

    sensors = []

    for subject in subjects:
        subject_grades = grades_by_subject.get(subject.subject_id, [])
        sensors.append(
            EduPageSubjectSensor(
                coordinator,
                student.get("id"),
                student.get("name"),
                subject.name,
                subject_grades,
            )
        )

    sensors.append(
        EduPageNotificationSensor(
            coordinator, student.get("id"), student.get("name"), notifications
        )
    )

    async_add_entities(sensors, True)


class EduPageSubjectSensor(CoordinatorEntity, SensorEntity):
    """Subject sensor entity for a specific student."""

    def __init__(self, coordinator, student_id, student_name, subject_name, grades=None):
        """Initialize the sensor."""
        super().__init__(coordinator)

        self._student_id = student_id
        self._student_name = unidecode(student_name).replace(" ", "_").lower()
        self._subject_name = unidecode(subject_name).replace(" ", "_").lower()
        self._grades = grades or []

        self._attr_name = f"Edupage - {student_name} - {subject_name}"
        self._name = self._attr_name

        self._unique_id = f"edupage_subject_{self._student_id}_{self._student_name}_{self._subject_name}"

    @property
    def unique_id(self):
        """Return a unique identifier for this sensor."""
        return self._unique_id

    @property
    def state(self):
        """Return the grade count."""
        return len(self._grades)

    @property
    def extra_state_attributes(self):
        """Return additional attributes."""
        if not self._grades:
            return {"info": "no grades yet"}

        attributes = {
            "student": self.coordinator.data.get("student", {}),
            "unique_id": self._unique_id,
        }

        for i, grade in enumerate(self._grades):
            attributes[f"grade_{i+1}_title"] = grade.title
            attributes[f"grade_{i+1}_grade_n"] = grade.grade_n
            if grade.max_points:
                attributes[f"grade_{i+1}_max_points"] = grade.max_points
            if grade.class_grade_avg:
                attributes[f"grade_{i+1}_class_avg_grade"] = grade.class_grade_avg
            if grade.percent:
                attributes[f"grade_{i+1}_percent"] = grade.percent
            if grade.comment:
                attributes[f"grade_{i+1}_comment"] = grade.comment
            attributes[f"grade_{i+1}_date"] = grade.date.strftime("%Y-%m-%d %H:%M:%S")

            teacher_name = grade.teacher.name if grade.teacher else "unknown"
            attributes[f"grade_{i+1}_teacher"] = teacher_name

        return attributes


class EduPageNotificationSensor(CoordinatorEntity, SensorEntity):
    """Notification sensor for a specific student (counts all event types)."""

    def __init__(self, coordinator, student_id, student_name, notifications):
        """Initialize the sensor."""
        super().__init__(coordinator)

        self._notifications = notifications
        self._student_id = student_id
        self._student_name = unidecode(student_name).replace(" ", "_").lower()

        self._attr_name = f"Edupage - Notification {student_name}"
        self._name = self._attr_name

        self._unique_id = f"edupage_notification_{self._student_id}_{self._student_name}"

    @property
    def unique_id(self):
        """Return a unique identifier for this sensor."""
        return self._unique_id

    @property
    def _current_notifications(self):
        """Return the latest notifications from the coordinator."""
        return self.coordinator.data.get("notifications", [])

    @property
    def state(self):
        """Return state."""
        return len(self._current_notifications)

    @property
    def extra_state_attributes(self):
        """Return additional attributes."""
        notifications = self._current_notifications
        attributes = {
            "student": self.coordinator.data.get("student", {}),
            "unique_id": self._unique_id,
            "event_count": len(notifications),
        }

        # Per-type breakdown of all notification types.
        type_counts = defaultdict(int)
        for event in notifications:
            event_type = getattr(event.event_type, "value", None) or str(event.event_type)
            type_counts[event_type] += 1
        attributes["type_counts"] = dict(type_counts)

        # Structured, aggregated list of recent events — convenient for templates
        # and dashboards (e.g. `state_attr(..., 'events')`). Capped to keep
        # stored state attributes bounded over time.
        events = []
        for event in notifications[:_MAX_EVENTS]:
            item = {
                "id": event.event_id,
                "type": getattr(event.event_type, "value", None) or str(event.event_type),
                "text": event.text,
                "timestamp": event.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            }
            if event.additional_data:
                if "date" in event.additional_data:
                    item["deadline"] = event.additional_data["date"]
                if "predmetid" in event.additional_data:
                    subject_id = event.additional_data["predmetid"]
                    item["subject"] = self._subject_name_by_id(subject_id)
            if event.author:
                item["author"] = (
                    event.author.name if hasattr(event.author, "name") else event.author
                )
            events.append(item)
        attributes["events"] = events

        for i, event in enumerate(notifications):
            attributes[f"event_{i+1}_id"] = event.event_id
            attributes[f"event_{i+1}_type"] = (
                getattr(event.event_type, "value", None) or str(event.event_type)
            )
            attributes[f"event_{i+1}_text"] = event.text
            attributes[f"event_{i+1}_timestamp"] = event.timestamp.strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            if event.additional_data:
                if "date" in event.additional_data:
                    attributes[f"event_{i+1}_deadline"] = event.additional_data["date"]
                if "predmetid" in event.additional_data:
                    subject_id = event.additional_data["predmetid"]
                    subject_name = self._subject_name_by_id(subject_id)
                    attributes[f"event_{i+1}_subject"] = subject_name
            if event.author:
                author_name = (
                    event.author.name if hasattr(event.author, "name") else event.author
                )
                attributes[f"event_{i+1}_author"] = author_name

        return attributes

    def _subject_name_by_id(self, subject_id):
        for subject in self.coordinator.data.get("subjects", []):
            if subject.subject_id == subject_id:
                return subject.name
        return None