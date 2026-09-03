import logging
from datetime import datetime, timedelta, date
from typing import Optional

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from .const import DOMAIN
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

    async_add_entities(calendars)

    _LOGGER.debug("CALENDAR async_setup_entry finished.")


class EdupageCalendar(CoordinatorEntity, CalendarEntity):
    """Representation of an Edupage calendar entity."""

    def __init__(self, coordinator, data):
        super().__init__(coordinator)
        self._data = data
        self._events = []
        self._attr_name = "Edupage Calendar"

    @property
    def unique_id(self):
        """Return a unique ID for this calendar."""
        student_id = self.coordinator.data.get("student", {}).get("id", "unknown")
        return f"edupage_calendar_{student_id}"

    @property
    def name(self):
        """Return the name of the calendar."""
        student_name = self.coordinator.data.get("student", {}).get("name", "Unknown Student")
        return f"Edupage - {student_name}"

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

    @property
    def unique_id(self):
        """Return a unique ID for this calendar."""
        student_id = self.coordinator.data.get("student", {}).get("id", "unknown")
        return f"edupage_canteen_calendar_{student_id}"

    @property
    def name(self):
        """Return the name of the calendar."""
        student_name = self.coordinator.data.get("student", {}).get("name", "Unknown Student")
        return f"Edupage Canteen - {student_name}"

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