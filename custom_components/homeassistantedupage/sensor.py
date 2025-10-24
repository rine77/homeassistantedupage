import logging
import re
from unidecode import unidecode
from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN
from collections import defaultdict

_LOGGER = logging.getLogger("custom_components.homeassistant_edupage")

def group_grades_by_subject(grades):
    """grouping grades based on subject_id."""
    grouped = defaultdict(list)
    for grade in grades:
        grouped[grade.subject_id].append(grade)
    return grouped

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
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
        sensor = EduPageSubjectSensor(
            coordinator,
            student.get("id"),
            student.get("name"),
            subject.name,
            subject_grades
        )
        sensors.append(sensor)

    notify = EduPageNotificationSensor(
        coordinator,
        student.get("id"),
        student.get("name"),
        notifications,
        subjects
    )

    sensors.append(notify)

    async_add_entities(sensors, True)

class EduPageSubjectSensor(CoordinatorEntity, SensorEntity):
    """Subject sensor entity for a specific student."""

    def __init__(self, coordinator, student_id, student_name, subject_name, grades=None):
        """Initialize the sensor."""
        super().__init__(coordinator)

        self._student_id = student_id
        self._student_name = unidecode(student_name).replace(' ', '_').lower()
        self._subject_name = unidecode(subject_name).replace(' ', '_').lower()
        self._grades = grades or []

        self._attr_name = f"Edupage - {student_name} - {subject_name}"
        self._name = self._attr_name

        self._unique_id = f"edupage_subject_{self._student_id}_{self._student_name}_{self._subject_name}"

        _LOGGER.info("SENSOR unique_id %s", self._unique_id)

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

        attributes = {}

        attributes = {
            "student": self.coordinator.data.get("student", {}),
            "unique_id": self._unique_id
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
    """Subject sensor entity for a specific student."""

    def __init__(self, coordinator, student_id, student_name, notifications, subjects):
        """Initialize the sensor."""
        super().__init__(coordinator)

        self._notifications = notifications
        self._student_id = student_id
        self._student_name = unidecode(student_name).replace(' ', '_').lower()

        self._attr_name = f"Edupage - Notification {student_name}"
        self._name = self._attr_name

        self._unique_id = f"edupage_notification_{self._student_id}_{self._student_name}"
        self._subjects = {subject.subject_id: subject.name for subject in subjects}

        _LOGGER.info("SENSOR unique_id %s", self._unique_id)

    @property
    def unique_id(self):
        """Return a unique identifier for this sensor."""
        return self._unique_id

    @property
    def state(self):
        """Return state."""
        return len(self._notifications)

    @property
    def extra_state_attributes(self):
        """Return additional attributes."""

        attributes = {}

        attributes = {
            "student": self.coordinator.data.get("student", {}),
            "unique_id": self._unique_id
            }

        for i, event in enumerate(self._notifications):
            if event.event_type == EventType.HOMEWORK:
                attributes[f"event_{i+1}_id"] = event.event_id
                attributes[f"event_{i+1}_text"] = event.text
                attributes[f"event_{i+1}_timestamp"] = event.timestamp.strftime("%Y-%m-%d %H:%M:%S")
                attributes[f"event_{i+1}_deadline"] = event.additional_data.get("date") if event.additional_data else None
                author_name = event.author if event.author else "no author"
                attributes[f"event_{i+1}_author"] = author_name
                attributes[f"event_{i+1}_subject"] = self._subjects.get(int(event.additional_data.get("predmetid"))) if event.additional_data else None

        return attributes

class EventType:
    HOMEWORK = 'homework'