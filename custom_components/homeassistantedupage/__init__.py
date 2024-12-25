import logging
import asyncio
from datetime import datetime, timedelta
from edupage_api.exceptions import BadCredentialsException, CaptchaException
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from .homeassistant_edupage import Edupage
from edupage_api.lunches import Lunch

from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from .const import DOMAIN, CONF_PHPSESSID, CONF_SUBDOMAIN, CONF_STUDENT_ID

_LOGGER = logging.getLogger("custom_components.homeassistant_edupage")

async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """only ConfigEntry supported, no configuration.yaml yet"""
    _LOGGER.debug("INIT called async_setup")
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """initializin EduPage-integration and validate API-login"""
    _LOGGER.debug("INIT called async_setup_entry")
    if DOMAIN not in hass.data:
        hass.data[DOMAIN] = {}

    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]
    subdomain = entry.data[CONF_SUBDOMAIN]
    PHPSESSID = entry.data[CONF_PHPSESSID]
    student_id = entry.data[CONF_STUDENT_ID]
    edupage = Edupage(hass=hass, sessionid=PHPSESSID)
    coordinator = None

    try:
        await edupage.login(username, password, subdomain)
        _LOGGER.debug("INIT login_success")

    except BadCredentialsException as e:
        _LOGGER.error("INIT login failed: bad credentials. %s", e)
        return False

    except CaptchaException as e:
        _LOGGER.error("INIT login failed: CAPTCHA needed. %s", e)
        return False

    except Exception as e:
        _LOGGER.error("INIT unexpected login error: %s", e.with_traceback(None))
        return False

    fetch_lock = asyncio.Lock()

    async def fetch_data():
        """Function to fetch timetable data for the selected student."""
        _LOGGER.debug("INIT called fetch_data")

        async with fetch_lock:
            try:
                await edupage.login(username, password, subdomain)

                students = await edupage.get_students()
                student = next((s for s in students if s.person_id == student_id), None)
                _LOGGER.debug("INIT Student: %s", student)

                if not student:
                    _LOGGER.error("INIT No matching student found with ID: %s", student_id)
                    return {"timetable": {}}

                _LOGGER.debug("INIT Found EduStudent: %s", vars(student))

                grades = await edupage.get_grades()
                subjects = await edupage.get_subjects()
                notifications = await edupage.get_notifications()

                timetable_data = {}
                timetable_data_canceled = {}
                today = datetime.now().date()
                for offset in range(14):
                    current_date = today + timedelta(days=offset)
                    timetable = await edupage.get_timetable(student, current_date)
                    lessons_to_add = []
                    canceled_lessons = []
                    if timetable is not None:
                        for lesson in timetable:
                            if not lesson.is_cancelled:
                                lessons_to_add.append(lesson)
                            else:
                                canceled_lessons.append(lesson)
                    if lessons_to_add:
                        _LOGGER.debug(f"Timetable for {current_date}: {lessons_to_add}")
                        timetable_data[current_date] = lessons_to_add
                    else:
                        _LOGGER.warning(f"INIT No timetable found for {current_date}")
                    if canceled_lessons:
                        timetable_data_canceled[current_date] = canceled_lessons

                canteen_menu_data = {}
                canteen_calendar_enabled = True
                for offset in range(14):
                    current_date = today + timedelta(days=offset)
                    try:
                        lunch = await edupage.get_lunches(current_date)
                    except Exception as e:
                        _LOGGER.error(f"Failed to fetch lunch data for {current_date}: {e}")
                        lunch = None
                        canteen_calendar_enabled = False
                        break
                    meals_to_add = []
                    if lunch is not None and lunch.menus is not None and len(lunch.menus) > 0:
                        _LOGGER.debug(f"Lunch for {current_date}: {lunch}")
                        meals_to_add.append(lunch)

                    if meals_to_add:
                        _LOGGER.debug(f"Daily menu for {current_date}: {lessons_to_add}")
                        canteen_menu_data[current_date] = meals_to_add
                    else:
                        _LOGGER.warning(f"INIT No daily menu found for {current_date}")

                return_data = {
                    "student": {"id": student.person_id, "name": student.name},
                    "grades": grades,
                    "subjects": subjects,
                    "timetable": timetable_data,
                    "canteen_menu": canteen_menu_data,
                    "canteen_calendar_enabled": canteen_calendar_enabled,
                    "cancelled_lessons": timetable_data_canceled,
                    "notifications": notifications,
                }
                _LOGGER.debug(f"INIT Coordinator fetch_data returning: {return_data}")
                return return_data

            except Exception as e:
                _LOGGER.error("INIT Failed to fetch timetable: %s", e)
                return {"timetable": {}}

    try:
        coordinator = DataUpdateCoordinator(
            hass,
            _LOGGER,
            name="Edupage",
            update_method=fetch_data,
            update_interval=timedelta(minutes=30),
        )
        hass.data[DOMAIN][entry.entry_id] = coordinator

        await coordinator.async_config_entry_first_refresh()
        _LOGGER.debug("INIT Coordinator successfully initialized")

    except Exception as e:
        _LOGGER.error(f"INIT Error during async_setup_entry: {e}")

        if entry.entry_id in hass.data[DOMAIN]:
            del hass.data[DOMAIN][entry.entry_id]

        return False

    await asyncio.sleep(1)
    await coordinator.async_config_entry_first_refresh()
    _LOGGER.debug("INIT Coordinator first fetch!")

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, ["calendar", "sensor"])
    _LOGGER.debug("INIT forwarded")
    _LOGGER.debug(f"INIT Coordinator data: {coordinator.data}")

    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload ConfigEntry."""
    _LOGGER.debug("INIT called async_unload_entry")

    unload_calendar = await hass.config_entries.async_forward_entry_unload(entry, "calendar")
    unload_sensor = await hass.config_entries.async_forward_entry_unload(entry, "sensor")

    unload_ok = unload_calendar and unload_sensor

    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
