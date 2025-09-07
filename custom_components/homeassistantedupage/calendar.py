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
_LOGGER.debug("CALENDAR Edupage calendar.py is being loaded")

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up Edupage calendar entities."""
    _LOGGER.debug("CALENDAR called async_setup_entry")

    coordinator = hass.data[DOMAIN][entry.entry_id]

    calendars = []

    edupage_calendar = EdupageCalendar(coordinator, entry.data)
    calendars.append(edupage_calendar)

    if coordinator.data.get("canteen_calendar_enabled", {}):
        edupage_canteen_calendar = EdupageCanteenCalendar(coordinator, entry.data)
        calendars.append(edupage_canteen_calendar)
        _LOGGER.debug("Canteen calendar added")
    else:
        _LOGGER.debug("Canteen calendar skipped, calendar disabled due to exceptions")

    async_add_entities(calendars)

    _LOGGER.debug("CALENDAR async_setup_entry finished.")

async def async_added_to_hass(self) -> None:
    """When entity is added to hass."""
    await super().async_added_to_hass()
    _LOGGER.debug("CALENDAR added to hass")

    if self.coordinator:
        self.async_on_remove(
            self.coordinator.async_add_listener(
                self._handle_coordinator_update, None
            )
        )

class EdupageCalendar(CoordinatorEntity, CalendarEntity):
    """Representation of an Edupage calendar entity."""

    def __init__(self, coordinator, data):
        super().__init__(coordinator)
        self._data = data
        self._events = []
        self._attr_name = "Edupage Calendar"
        _LOGGER.debug(f"CALENDAR Initialized EdupageCalendar with data: {data}")

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
    def extra_state_attributes(self):
        """Return the extra state attributes."""
        return {
            "unique_id": self.unique_id,
            "other_info": "debug info"
        }

    @property
    def available(self) -> bool:
        """Return True if the calendar is available."""
        _LOGGER.debug("CALENDAR Checking availability of Edupage Calendar")
        return True

    @property
    def event(self):
        """Return the next upcoming event or None if no event exists."""
        return self.find_lesson_now_or_next_across_days()

    async def async_get_events(self, hass, start_date: datetime, end_date: datetime):
        """Return events in a specific date range."""
        events = []

        _LOGGER.debug(f"CALENDAR Fetching events between {start_date} and {end_date}")
        timetable = self.coordinator.data.get("timetable", {})
        timetable_canceled = self.coordinator.data.get("cancelled_lessons", {})
        _LOGGER.debug(f"CALENDAR Coordinator data: {self.coordinator.data}")
        _LOGGER.debug(f"CALENDAR Fetched timetable data: {timetable}")

        if not timetable:
            _LOGGER.warning("CALENDAR Timetable data is missing.")
            return events

        current_date = start_date.date()
        while current_date <= end_date.date():
            events.extend(self.get_events(timetable, current_date))
            events.extend(self.get_events(timetable_canceled, current_date))
            current_date += timedelta(days=1)

        _LOGGER.debug(f"CALENDAR Fetched {len(events)} events from {start_date} to {end_date}")
        return events

    def get_events(self, timetable, current_date):
        events = []
        day_timetable = timetable.get(current_date)
        if day_timetable:
            for lesson in day_timetable:

                _LOGGER.debug(f"CALENDAR Lesson attributes: {vars(lesson)}")

                events.append(
                    self.map_lesson_to_calender_event(lesson, current_date)
                )
        return events


    def map_lesson_to_calender_event(self, lesson: Lesson, day: date) -> CalendarEvent:
        teacher_names = [teacher.name for teacher in lesson.teachers] if lesson.teachers else []
        teachers = ", ".join(teacher_names) if teacher_names else "Unknown Teacher"
        description=f"Teacher(s): {teachers}"
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
            summary= lesson_subject_prefix + lesson_subject,
            description=description,
            location=room
        )
        return cal_event

    # Funktion zum Filtern und Finden der nächsten passenden Lesson
    def find_lesson_now_or_next_across_days(self) -> Optional[CalendarEvent]:
        lessons_by_day = self.coordinator.data.get("timetable", {})
        current_time = datetime.now().time()  # Aktuelle Uhrzeit
        current_day = datetime.date(datetime.now())  # Aktueller Wochentag

        # Schritt 1: Suche im aktuellen Tag
        lessons_today = lessons_by_day.get(current_day, [])
        current_lesson = next((lesson for lesson in lessons_today
                               if lesson.start_time <= current_time <= lesson.end_time), None)

        if current_lesson:
            return self.map_lesson_to_calender_event(current_lesson, current_day)  # Falls eine aktuelle Lesson gefunden wird

        # Schritt 2: Finde die nächste Lesson heute oder an zukünftigen Tagen
        next_lesson = None
        for day, lessons in sorted(lessons_by_day.items(), key=lambda x: x[0]):  # Nach Tagen sortieren
            # Überspringe vergangene Tage
            if day < current_day:
                continue

            # Prüfe Lektionen heute (nach current_time) oder in zukünftigen Tagen
            future_lessons = [lesson for lesson in lessons if day > current_day or lesson.start_time > current_time]
            if future_lessons:
                # Die früheste Lesson finden
                next_lesson_today_or_future = min(future_lessons, key=lambda x: x.start_time)
                if next_lesson is None or next_lesson_today_or_future.start_time < next_lesson.start_time:
                    next_lesson = next_lesson_today_or_future

                if next_lesson:
                    return self.map_lesson_to_calender_event(next_lesson, day)

        return None

class EdupageCanteenCalendar(CoordinatorEntity, CalendarEntity):
    """Representation of an Edupage canteen calendar entity."""

    def __init__(self, coordinator, data):
        super().__init__(coordinator)
        self._data = data
        self._events = []
        self._attr_name = "Edupage Canteen Calendar"
        _LOGGER.debug(f"CALENDAR Initialized EdupageCanteenCalendar with data: {data}")

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
    def extra_state_attributes(self):
        """Return the extra state attributes."""
        return {
            "unique_id": self.unique_id,
            "other_info": "debug info"
        }

    @property
    def available(self) -> bool:
        """Return True if the calendar is available."""
        _LOGGER.debug("CALENDAR Checking availability of Edupage Canteen Calendar")
        return True

    @property
    def event(self):
        """Return the next upcoming event or None if no event exists."""
        return self.find_meal_now_or_next_across_days()

    async def async_get_events(self, hass, start_date: datetime, end_date: datetime):
        """Return events in a specific date range."""
        events = []

        _LOGGER.debug(f"CALENDAR Fetching canteen calendar between {start_date} and {end_date}")
        canteen_menu = self.coordinator.data.get("canteen_menu", {})
        _LOGGER.debug(f"CALENDAR Coordinator data: {self.coordinator.data}")
        _LOGGER.debug(f"CALENDAR Fetched canteen_menu data: {canteen_menu}")

        if not canteen_menu:
            _LOGGER.warning("CALENDAR Canteen menu data is missing.")
            return events

        current_date = start_date.date()
        while current_date <= end_date.date():
            events.extend(self.get_events(canteen_menu, current_date))
            current_date += timedelta(days=1)

        _LOGGER.debug(f"CALENDAR Fetched {len(events)} events from {start_date} to {end_date}")
        return events

    def get_events(self, canteen_menu, current_date):
        events = []
        daily_menu = canteen_menu.get(current_date)
        if daily_menu:
            for meal in daily_menu:
                _LOGGER.debug(f"CALENDAR Meal attributes: {vars(meal)}")
                events.append(
                    self.map_meal_to_calender_event(meal, current_date)
                )
        return events


    def map_meal_to_calender_event(self, meal: Meal, day: date) -> CalendarEvent:
        local_tz = ZoneInfo(self.hass.config.time_zone)
        start_time = datetime.combine(day, meal.served_from.time()).astimezone(local_tz)
        end_time = datetime.combine(day, meal.served_to.time()).astimezone(local_tz)
        summary = meal.meal_type.name
        description = meal.title

        cal_event = CalendarEvent(
            start=start_time,
            end=end_time,
            summary=summary,
            description=description
        )
        return cal_event

    def find_meal_now_or_next_across_days(self) -> Optional[CalendarEvent]:
        # TODO implement
        return None