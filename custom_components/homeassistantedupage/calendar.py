import logging
from datetime import date, datetime, time, timedelta
from typing import Any, Optional

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from .const import CONF_STUDENT_ID, CONF_STUDENT_NAME, DOMAIN
from .entity_helpers import student_device_info
from .event import _event_type_value
from zoneinfo import ZoneInfo
from edupage_api.timetables import Lesson
from edupage_api.lunches import Meal

_LOGGER = logging.getLogger("custom_components.homeassistant_edupage")

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up Edupage calendar entities."""
    _LOGGER.debug("CALENDAR called async_setup_entry")

    coordinator = hass.data[DOMAIN][entry.entry_id]

    calendars = []

    edupage_calendar = EdupageCalendar(coordinator, entry.data)
    calendars.append(edupage_calendar)

    edupage_canteen_calendar = EdupageCanteenCalendar(coordinator, entry.data)
    calendars.append(edupage_canteen_calendar)

    edupage_assignments_calendar = EduPageAssignmentsCalendar(
        coordinator, entry.data
    )
    calendars.append(edupage_assignments_calendar)

    async_add_entities(calendars)

    _LOGGER.debug("CALENDAR async_setup_entry finished.")


class EdupageCalendar(CoordinatorEntity, CalendarEntity):
    """Representation of an Edupage calendar entity."""

    def __init__(self, coordinator, data):
        super().__init__(coordinator)
        self._data = data
        self._events = []
        self._attr_name = "Edupage Calendar"
        student = coordinator.data.get("student", {}) if coordinator.data else {}
        self._student_id = student.get("id", data.get(CONF_STUDENT_ID, "unknown"))
        self._student_name = student.get("name") or data.get(
            CONF_STUDENT_NAME, "Unknown Student"
        )
        self._attr_device_info = student_device_info(
            self._student_id, self._student_name
        )

    @property
    def unique_id(self):
        """Return a unique ID for this calendar."""
        return f"edupage_calendar_{self._student_id}"

    @property
    def name(self):
        """Return the name of the calendar."""
        return f"Edupage - {self._student_name}"

    @property
    def available(self) -> bool:
        """Return True if the calendar is available."""
        return True

    @property
    def event(self):
        """Return the next upcoming event or None if no event exists."""
        return self.find_lesson_now_or_next_across_days()

    async def async_get_events(self, hass, start_date: datetime, end_date: datetime):
        """Return events in a specific date range."""
        events = []

        timetable = self.coordinator.data.get("timetable", {})
        timetable_canceled = self.coordinator.data.get("cancelled_lessons", {})

        if not timetable:
            _LOGGER.warning("CALENDAR Timetable data is missing.")
            return events

        current_date = start_date.date()
        while current_date <= end_date.date():
            events.extend(self.get_events(timetable, current_date))
            events.extend(self.get_events(timetable_canceled, current_date))
            current_date += timedelta(days=1)

        return events

    def get_events(self, timetable, current_date):
        events = []
        day_timetable = timetable.get(current_date)
        if day_timetable:
            for lesson in day_timetable:
                events.append(
                    self.map_lesson_to_calender_event(lesson, current_date)
                )
        return events

    def map_lesson_to_calender_event(self, lesson: Lesson, day: date) -> CalendarEvent:
        teacher_names = [teacher.name for teacher in lesson.teachers] if lesson.teachers else []
        teachers = ", ".join(teacher_names) if teacher_names else "Unknown Teacher"
        description = f"Teacher(s): {teachers}"
        room = None
        if lesson.classrooms:
            room = lesson.classrooms[0].name
            description += f"\nRoom: {room}"
        local_tz = ZoneInfo(self.hass.config.time_zone)
        start_time = datetime.combine(day, lesson.start_time).astimezone(local_tz)
        end_time = datetime.combine(day, lesson.end_time).astimezone(local_tz)
        lesson_subject = lesson.subject.name if lesson.subject else "Unknown Subject"
        lesson_subject_prefix = "[Canceled] " if lesson.is_cancelled else ""

        cal_event = CalendarEvent(
            start=start_time,
            end=end_time,
            summary=lesson_subject_prefix + lesson_subject,
            description=description,
            location=room,
        )
        return cal_event

    def find_lesson_now_or_next_across_days(self) -> Optional[CalendarEvent]:
        lessons_by_day = self.coordinator.data.get("timetable", {})
        current_time = datetime.now().time()
        current_day = datetime.now().date()

        # Step 1: look for a lesson currently in progress.
        lessons_today = lessons_by_day.get(current_day, [])
        current_lesson = next(
            (
                lesson
                for lesson in lessons_today
                if lesson.start_time <= current_time <= lesson.end_time
            ),
            None,
        )
        if current_lesson:
            return self.map_lesson_to_calender_event(current_lesson, current_day)

        # Step 2: find the next lesson today or on a future day.
        # Compare full datetimes (day + start_time) so a lesson today at 10:00
        # is always selected ahead of a lesson tomorrow at 08:00.
        next_lesson_day = None
        next_lesson = None
        next_lesson_start = None
        for day, lessons in sorted(lessons_by_day.items(), key=lambda x: x[0]):
            if day < current_day:
                continue
            future_lessons = [
                lesson
                for lesson in lessons
                if day > current_day or lesson.start_time > current_time
            ]
            if not future_lessons:
                continue
            candidate_start = min(
                (datetime.combine(day, lesson.start_time) for lesson in future_lessons)
            )
            if next_lesson_start is None or candidate_start < next_lesson_start:
                next_lesson_start = candidate_start
                next_lesson = next(
                    lesson
                    for lesson in future_lessons
                    if datetime.combine(day, lesson.start_time) == candidate_start
                )
                next_lesson_day = day

        if next_lesson and next_lesson_day:
            return self.map_lesson_to_calender_event(next_lesson, next_lesson_day)

        return None


class EdupageCanteenCalendar(CoordinatorEntity, CalendarEntity):
    """Representation of an Edupage canteen calendar entity."""

    def __init__(self, coordinator, data):
        super().__init__(coordinator)
        self._data = data
        self._events = []
        self._attr_name = "Edupage Canteen Calendar"
        student = coordinator.data.get("student", {}) if coordinator.data else {}
        self._student_id = student.get("id", data.get(CONF_STUDENT_ID, "unknown"))
        self._student_name = student.get("name") or data.get(
            CONF_STUDENT_NAME, "Unknown Student"
        )
        self._attr_device_info = student_device_info(
            self._student_id, self._student_name
        )

    @property
    def unique_id(self):
        """Return a unique ID for this calendar."""
        return f"edupage_canteen_calendar_{self._student_id}"

    @property
    def name(self):
        """Return the name of the calendar."""
        return f"Edupage Canteen - {self._student_name}"

    @property
    def available(self) -> bool:
        """Return True if the calendar is available."""
        return True

    @property
    def event(self):
        """Return the next upcoming meal event or None if none exists."""
        return self.find_meal_now_or_next_across_days()

    async def async_get_events(self, hass, start_date: datetime, end_date: datetime):
        """Return canteen meal events in a specific date range."""
        events = []

        canteen_menu = self.coordinator.data.get("canteen_menu", {})

        if not canteen_menu:
            _LOGGER.warning("CALENDAR Canteen menu data is missing.")
            return events

        current_date = start_date.date()
        while current_date <= end_date.date():
            events.extend(self.get_events(canteen_menu, current_date))
            current_date += timedelta(days=1)

        return events

    def get_events(self, canteen_menu, current_date):
        events = []
        daily_menu = canteen_menu.get(current_date)
        if daily_menu:
            for meal in daily_menu:
                events.append(self.map_meal_to_calender_event(meal, current_date))
        return events

    def map_meal_to_calender_event(self, meal: Meal, day: date) -> Optional[CalendarEvent]:
        local_tz = ZoneInfo(self.hass.config.time_zone)
        if meal.served_from is None or meal.served_to is None:
            # No serving window exposed by EduPage; show an all-event-day slot.
            start_time = datetime.combine(day, datetime.min.time()).astimezone(local_tz)
            end_time = start_time + timedelta(days=1)
        else:
            start_time = datetime.combine(day, meal.served_from.time()).astimezone(local_tz)
            end_time = datetime.combine(day, meal.served_to.time()).astimezone(local_tz)
            if end_time <= start_time:
                end_time = end_time + timedelta(days=1)

        summary = meal.meal_type.name.replace("_", " ").capitalize()
        description = meal.title

        return CalendarEvent(
            start=start_time,
            end=end_time,
            summary=summary,
            description=description,
        )

    def find_meal_now_or_next_across_days(self) -> Optional[CalendarEvent]:
        canteen_menu = self.coordinator.data.get("canteen_menu", {})
        now = datetime.now()
        today = now.date()

        # Each entry is (day, meal, start_datetime_or_None, end_datetime_or_None).
        # All values are datetimes (or None) so they can be compared against
        # ``now`` without mixing ``datetime.time`` and ``datetime.datetime``.
        meals_by_time = []
        for day, meals in canteen_menu.items():
            for meal in meals:
                if day < today:
                    continue
                if meal.served_from is None:
                    # No serving window exposed by EduPage: the meal occupies
                    # the whole day (midnight -> next midnight).
                    start_dt = datetime.combine(day, datetime.min.time())
                    end_dt = start_dt + timedelta(days=1)
                    meals_by_time.append((day, meal, start_dt, end_dt))
                    continue
                start_dt = datetime.combine(day, meal.served_from.time())
                end_dt = (
                    datetime.combine(day, meal.served_to.time())
                    if meal.served_to
                    else None
                )
                meals_by_time.append((day, meal, start_dt, end_dt))

        # A meal currently being served is the "event" right now.
        for day, meal, start_dt, end_dt in meals_by_time:
            if day == today and start_dt is not None and end_dt is not None:
                if start_dt <= now <= end_dt:
                    return self.map_meal_to_calender_event(meal, day)

        # Otherwise the next future meal.
        next_meal = None
        next_day = None
        next_start = None
        for day, meal, start_dt, end_dt in meals_by_time:
            candidate = start_dt or datetime.combine(day, datetime.min.time())
            if candidate > now:
                if next_start is None or candidate < next_start:
                    next_start = candidate
                    next_meal = meal
                    next_day = day

        if next_meal and next_day:
            return self.map_meal_to_calender_event(next_meal, next_day)

        return None


_HOMEWORK_TYPE = "homework"
_EXAM_TYPES = {
    "bexam",
    "oexam",
    "rexam",
    "pexam",
    "sexam",
    "testing",
    "testpridelenie",
}


def _parse_notification_date(value: Any) -> date | None:
    """Parse the date portion of an EduPage notification deadline."""
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


class EduPageAssignmentsCalendar(CoordinatorEntity, CalendarEntity):
    """Expose homework deadlines and exams as an EduPage calendar."""

    def __init__(self, coordinator, data):
        """Initialize the assignments calendar."""
        super().__init__(coordinator)
        self._data = data
        student = coordinator.data.get("student", {}) if coordinator.data else {}
        self._student_id = student.get("id", data.get(CONF_STUDENT_ID, "unknown"))
        self._student_name = student.get("name") or data.get(
            CONF_STUDENT_NAME, "Unknown Student"
        )
        self._attr_name = f"EduPage - Assignments {self._student_name}"
        self._attr_unique_id = f"edupage_assignments_{self._student_id}"
        self._attr_device_info = student_device_info(
            self._student_id, self._student_name
        )

    @property
    def available(self) -> bool:
        """Return True because an empty assignments calendar is valid."""
        return True

    def _subject_name(self, subject_id: Any) -> str | None:
        """Resolve a subject ID through the coordinator's subject list."""
        if subject_id is None or not self.coordinator.data:
            return None
        for subject in self.coordinator.data.get("subjects", []) or []:
            if str(getattr(subject, "subject_id", "")) == str(subject_id):
                return getattr(subject, "name", None)
        return None

    def _map_notification(self, notification: Any) -> CalendarEvent | None:
        """Map a supported EduPage notification to an all-day event."""
        raw_type = _event_type_value(notification)
        if raw_type != _HOMEWORK_TYPE and raw_type not in _EXAM_TYPES:
            return None

        additional_data = getattr(notification, "additional_data", None) or {}
        event_date = _parse_notification_date(additional_data.get("date"))
        if event_date is None:
            return None

        subject = self._subject_name(additional_data.get("predmetid"))
        text = str(getattr(notification, "text", None) or "Assignment")
        author = getattr(notification, "author", None)
        author_name = getattr(author, "name", None) or author

        kind = "Homework" if raw_type == _HOMEWORK_TYPE else "Exam"
        completed = raw_type == _HOMEWORK_TYPE and bool(
            getattr(notification, "is_done", False)
        )
        summary_parts = []
        if completed:
            summary_parts.append("[Completed]")
        summary_parts.append(f"[{kind}]")
        if subject:
            summary_parts.append(f"{subject}:")
        summary_parts.append(text)

        description_parts = [f"Type: {kind}"]
        if subject:
            description_parts.append(f"Subject: {subject}")
        if author_name:
            description_parts.append(f"Author: {author_name}")

        return CalendarEvent(
            start=event_date,
            end=event_date + timedelta(days=1),
            summary=" ".join(summary_parts),
            description="\n".join(description_parts),
        )

    def _calendar_events(self) -> list[CalendarEvent]:
        """Return all supported dated notifications in stable order."""
        if not self.coordinator.data:
            return []
        events = [
            event
            for notification in self.coordinator.data.get("notifications", []) or []
            if (event := self._map_notification(notification)) is not None
        ]
        return sorted(events, key=lambda event: (event.start, event.summary))

    @property
    def event(self) -> CalendarEvent | None:
        """Return today's or the next assignment or exam."""
        today = datetime.now(ZoneInfo(self.hass.config.time_zone)).date()
        return next(
            (event for event in self._calendar_events() if event.end > today),
            None,
        )

    async def async_get_events(
        self, hass, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        """Return assignments and exams overlapping the requested range."""
        start_day = start_date.date()
        end_day = end_date.date()
        if end_date.time() != time.min:
            end_day += timedelta(days=1)
        return [
            event
            for event in self._calendar_events()
            if event.end > start_day and event.start < end_day
        ]
