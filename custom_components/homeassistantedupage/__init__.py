import logging
import asyncio
from datetime import datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .homeassistant_edupage import Edupage, EdupageSessionExpired
from .const import DOMAIN, CONF_PHPSESSID, CONF_SUBDOMAIN, CONF_STUDENT_ID

_LOGGER = logging.getLogger("custom_components.homeassistant_edupage")


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the integration (config-entry only)."""
    _LOGGER.debug("INIT called async_setup")
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up EduPage integration and validate the stored session."""
    if DOMAIN not in hass.data:
        hass.data[DOMAIN] = {}

    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]
    subdomain = entry.data[CONF_SUBDOMAIN]
    phpsessid = entry.data[CONF_PHPSESSID]
    student_id = entry.data[CONF_STUDENT_ID]
    edupage = Edupage(hass=hass, sessionid=phpsessid)
    coordinator = None

    try:
        # Session-only: loads the stored PHPSESSID, never prompts for 2FA again.
        await edupage.login(username, password, subdomain, phpsessid)
        _LOGGER.debug("INIT login_success (session reloaded)")
    except EdupageSessionExpired as e:
        _LOGGER.error("INIT stored session invalid/expired: %s", e)
        await hass.config_entries.async_reauth(entry.entry_id)
        return False
    except Exception as e:  # noqa: BLE001
        _LOGGER.error("INIT session load failed: %s", e)
        return False

    fetch_lock = asyncio.Lock()

    async def fetch_data():
        """Fetch timetable, canteen and notification data for the student."""
        _LOGGER.debug("INIT called fetch_data")

        async with fetch_lock:
            try:
                await edupage.login(username, password, subdomain, phpsessid)

                students = await edupage.get_students()
                student = None
                if students:
                    student = next(
                        (s for s in students if s.person_id == student_id), None
                    )
                if student is None:
                    _LOGGER.error(
                        "INIT No matching student found with ID: %s", student_id
                    )
                    return {"timetable": {}}

                grades = await edupage.get_grades()
                subjects = await edupage.get_subjects()
                notifications = await edupage.get_notifications()

                today = datetime.now().date()

                timetable_data = {}
                timetable_data_canceled = {}
                for offset in range(14):
                    current_date = today + timedelta(days=offset)
                    try:
                        timetable = await edupage.get_timetable(
                            student, current_date
                        )
                    except Exception as e:  # noqa: BLE001
                        _LOGGER.error(
                            "Failed to fetch timetable data for %s: %s",
                            current_date,
                            e,
                        )
                        break
                    lessons_to_add = []
                    canceled_lessons = []
                    if timetable is not None:
                        for lesson in timetable:
                            if not lesson.is_cancelled:
                                lessons_to_add.append(lesson)
                            else:
                                canceled_lessons.append(lesson)
                    if lessons_to_add:
                        timetable_data[current_date] = lessons_to_add
                    if canceled_lessons:
                        timetable_data_canceled[current_date] = canceled_lessons

                canteen_menu_data = {}
                for offset in range(14):
                    current_date = today + timedelta(days=offset)
                    try:
                        meals = await edupage.get_meals(current_date)
                    except Exception as e:  # noqa: BLE001
                        _LOGGER.error(
                            "Failed to fetch meals data for %s: %s",
                            current_date,
                            e,
                        )
                        break
                    meals_to_add = []
                    if meals is not None:
                        for meal in (meals.snack, meals.lunch, meals.afternoon_snack):
                            if meal is not None and meal.menus:
                                meals_to_add.append(meal)
                    if meals_to_add:
                        canteen_menu_data[current_date] = meals_to_add

                return_data = {
                    "student": {"id": student.person_id, "name": student.name},
                    "grades": grades,
                    "subjects": subjects,
                    "timetable": timetable_data,
                    "canteen_menu": canteen_menu_data,
                    "canteen_calendar_enabled": bool(canteen_menu_data),
                    "cancelled_lessons": timetable_data_canceled,
                    "notifications": notifications,
                    "last_updated": datetime.now().isoformat(),
                }
                return return_data

            except EdupageSessionExpired as e:
                _LOGGER.error("INIT session expired during update: %s", e)
                await hass.config_entries.async_reauth(entry.entry_id)
                return {}
            except Exception as e:  # noqa: BLE001
                _LOGGER.error("INIT Failed: %s", e)
                return {}

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name="Edupage",
        update_method=fetch_data,
        update_interval=timedelta(minutes=30),
    )
    coordinator.edupage = edupage
    coordinator.fetch_lock = fetch_lock

    try:
        hass.data[DOMAIN][entry.entry_id] = coordinator
        await coordinator.async_config_entry_first_refresh()
        _LOGGER.debug("INIT Coordinator successfully initialized")
    except Exception as e:  # noqa: BLE001
        _LOGGER.error("INIT Error during async_setup_entry: %s", e)
        if entry.entry_id in hass.data[DOMAIN]:
            del hass.data[DOMAIN][entry.entry_id]
        return False

    await hass.config_entries.async_forward_entry_setups(entry, ["calendar", "sensor"])
    _LOGGER.debug("INIT forwarded")

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload ConfigEntry."""
    _LOGGER.debug("INIT called async_unload_entry")

    unload_calendar = await hass.config_entries.async_forward_entry_unload(
        entry, "calendar"
    )
    unload_sensor = await hass.config_entries.async_forward_entry_unload(entry, "sensor")

    unload_ok = unload_calendar and unload_sensor

    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)

    return unload_ok