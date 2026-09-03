import logging
from collections import defaultdict
from datetime import datetime

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
    subject_unique_ids = set()

    for subject in subjects:
        subject_grades = grades_by_subject.get(subject.subject_id, [])

        sensor = EduPageSubjectSensor(
            coordinator,
            student.get("id"),
            student.get("name"),
            subject.name,
            subject_grades,
        )

        if sensor.unique_id in subject_unique_ids:
            sensor._unique_id = f"{sensor.unique_id}_{subject.subject_id}"

        subject_unique_ids.add(sensor.unique_id)
        sensors.append(sensor)

    sensors.append(
        EduPageNotificationSensor(
            coordinator, student.get("id"), student.get("name"), notifications
        )
    )
    sensors.append(
        EduPageSubstitutionSensor(
            coordinator, student.get("id"), student.get("name"), "timetable_changes"
        )
    )
    sensors.append(
        EduPageSubstitutionSensor(
            coordinator, student.get("id"), student.get("name"), "missing_teachers"
        )
    )
    sensors.append(
        EduPageRingingSensor(coordinator, student.get("id"), student.get("name"))
    )
    sensors.append(
        EduPageTermAverageSensor(
            coordinator,
            student.get("id"),
            student.get("name"),
            term_key="first",
        )
    )
    sensors.append(
        EduPageTermAverageSensor(
            coordinator,
            student.get("id"),
            student.get("name"),
            term_key="second",
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

        # Flat per-event attributes are kept for backwards compatibility but
        # must also be bounded to avoid unbounded state-attribute growth.
        for i, event in enumerate(notifications[:_MAX_EVENTS]):
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


def _subject_slug(name):
    return unidecode(name).replace(" ", "_").lower()


def _grade_numeric(grade_n):
    """Best-effort numeric conversion of a grade value for averaging."""
    if grade_n is None:
        return None
    if isinstance(grade_n, (int, float)):
        return float(grade_n)
    try:
        return float(str(grade_n).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _average(grades):
    """Weighted-agnostic arithmetic mean of numeric grade values."""
    values = [v for v in (_grade_numeric(g.grade_n) for g in grades) if v is not None]
    return round(sum(values) / len(values), 2) if values else None


class EduPageSubstitutionSensor(CoordinatorEntity, SensorEntity):
    """Sensor for timetable changes or missing teachers for the current day."""

    def __init__(self, coordinator, student_id, student_name, data_key):
        """data_key is 'timetable_changes' or 'missing_teachers'."""
        super().__init__(coordinator)

        self._student_id = student_id
        self._student_name = _subject_slug(student_name)
        self._data_key = data_key
        label = (
            "Timetable Changes"
            if data_key == "timetable_changes"
            else "Missing Teachers"
        )

        self._attr_name = f"Edupage - {label} {student_name}"

        self._unique_id = f"edupage_{data_key}_{self._student_id}_{self._student_name}"

    @property
    def unique_id(self):
        """Return a unique identifier for this sensor."""
        return self._unique_id

    @property
    def state(self):
        return len(self.coordinator.data.get(self._data_key) or [])

    @property
    def extra_state_attributes(self):
        entries = self.coordinator.data.get(self._data_key) or []
        attributes = {
            "student": self.coordinator.data.get("student", {}),
            "unique_id": self._unique_id,
            "date": datetime.now().strftime("%Y-%m-%d"),
            "count": len(entries),
            "last_updated": self.coordinator.data.get("last_updated"),
        }
        if self._data_key == "timetable_changes":
            for i, change in enumerate(entries):
                attributes[f"change_{i+1}_class"] = change.change_class
                attributes[f"change_{i+1}_lesson"] = change.lesson_n
                attributes[f"change_{i+1}_title"] = change.title
                attributes[f"change_{i+1}_action"] = change.action
        else:
            for i, teacher in enumerate(entries):
                attributes[f"teacher_{i+1}_name"] = teacher.name
                attributes[f"teacher_{i+1}_id"] = teacher.person_id
        return attributes


class EduPageRingingSensor(CoordinatorEntity, SensorEntity):
    """Sensor exposing the next school-bell ringing time."""

    def __init__(self, coordinator, student_id, student_name):
        super().__init__(coordinator)
        self._student_id = student_id
        self._student_name = _subject_slug(student_name)
        self._attr_name = f"Edupage - Next Ringing {student_name}"
        self._unique_id = (
            f"edupage_next_ringing_{self._student_id}_{self._student_name}"
        )

    @property
    def unique_id(self):
        return self._unique_id

    @property
    def state(self):
        ringing = self.coordinator.data.get("next_ringing")
        if ringing is None:
            return "unknown"
        return ringing.time.strftime("%H:%M")

    @property
    def extra_state_attributes(self):
        ringing = self.coordinator.data.get("next_ringing")
        attrs = {
            "student": self.coordinator.data.get("student", {}),
            "unique_id": self._unique_id,
        }
        if ringing is not None:
            attrs["ringing_type"] = ringing.type.name
            attrs["time"] = ringing.time.strftime("%H:%M")
            attrs["date"] = datetime.now().strftime("%Y-%m-%d")
        return attrs


class EduPageTermAverageSensor(CoordinatorEntity, SensorEntity):
    """Sensor showing grade count and average for a specific school term.

    Reads the per-term grades live from the coordinator each poll, so the
    average follows later coordinator updates instead of freezing the initial
    snapshot.
    """

    def __init__(self, coordinator, student_id, student_name, term_key):
        super().__init__(coordinator)
        self._student_id = student_id
        self._student_name = _subject_slug(student_name)
        self._term_key = term_key
        term_label = "1st" if term_key == "first" else "2nd"

        self._attr_name = f"Edupage - {term_label} Term Average {student_name}"

        self._unique_id = (
            f"edupage_term_{term_key}_{self._student_id}_{self._student_name}"
        )

    @property
    def unique_id(self):
        return self._unique_id

    @property
    def _current_grades(self):
        return self.coordinator.data.get("grades_per_term", {}).get(
            self._term_key, []
        )

    @property
    def _school_year(self):
        return self.coordinator.data.get("school_year")

    @property
    def state(self):
        avg = _average(self._current_grades)
        return avg if avg is not None else "unknown"

    @property
    def extra_state_attributes(self):
        grades = self._current_grades
        by_subject = defaultdict(list)
        for grade in grades:
            by_subject[grade.subject_name or grade.subject_id].append(grade)

        subject_averages = {
            str(subject): _average(subject_grades)
            for subject, subject_grades in by_subject.items()
        }

        return {
            "student": self.coordinator.data.get("student", {}),
            "unique_id": self._unique_id,
            "school_year": self._school_year,
            "term": self._term_key,
            "grade_count": len(grades),
            "grade_average": _average(grades),
            "subject_averages": subject_averages,
        }