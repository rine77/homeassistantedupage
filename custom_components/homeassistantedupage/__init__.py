import logging
import asyncio
from datetime import datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_USERNAME
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
import voluptuous as vol
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .homeassistant_edupage import Edupage, EdupageSessionExpired
from .const import DOMAIN, CONF_PHPSESSID, CONF_SUBDOMAIN, CONF_STUDENT_ID
from edupage_api.lunches import MealType
from edupage_api.grades import Term

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
    subdomain = entry.data[CONF_SUBDOMAIN]
    phpsessid = entry.data[CONF_PHPSESSID]
    student_id = entry.data[CONF_STUDENT_ID]
    edupage = Edupage(hass=hass, sessionid=phpsessid)
    coordinator = None

    try:
        # Session-only: loads the stored PHPSESSID, never prompts for 2FA again.
        await edupage.login(username, subdomain, phpsessid)
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
                await edupage.login(username, subdomain, phpsessid)

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

                # Substitution / ringing extras for sensors.
                try:
                    timetable_changes = await edupage.get_timetable_changes(today)
                except Exception as e:  # noqa: BLE001
                    _LOGGER.warning(
                        "get_timetable_changes failed for %s: %s", today, e
                    )
                    timetable_changes = []

                try:
                    missing_teachers = await edupage.get_missing_teachers(today)
                except Exception as e:  # noqa: BLE001
                    _LOGGER.warning(
                        "get_missing_teachers failed for %s: %s", today, e
                    )
                    missing_teachers = []

                try:
                    next_ringing = await edupage.get_next_ringing_time(
                        datetime.now()
                    )
                except Exception as e:  # noqa: BLE001
                    _LOGGER.warning("get_next_ringing_time failed: %s", e)
                    next_ringing = None

                # Per-term grades for grade-average sensors.
                school_year = None
                grades_per_term = {}
                try:
                    school_year = await edupage.get_school_year()
                    term_map = {"first": Term.FIRST, "second": Term.SECOND}
                    for term_key, term_enum in term_map.items():
                        try:
                            grades_per_term[
                                term_key
                            ] = await edupage.get_grades_for_term(
                                school_year, term_enum
                            )
                        except Exception as e:  # noqa: BLE001
                            _LOGGER.warning(
                                "get_grades_for_term(%s) failed: %s", term_key, e
                            )
                            grades_per_term[term_key] = []
                except Exception as e:  # noqa: BLE001
                    _LOGGER.warning("get_school_year failed: %s", e)
                    school_year = None

                return_data = {
                    "student": {"id": student.person_id, "name": student.name},
                    "grades": grades,
                    "subjects": subjects,
                    "timetable": timetable_data,
                    "canteen_menu": canteen_menu_data,
                    "cancelled_lessons": timetable_data_canceled,
                    "notifications": notifications,
                    "timetable_changes": timetable_changes,
                    "missing_teachers": missing_teachers,
                    "next_ringing": next_ringing,
                    "school_year": school_year,
                    "grades_per_term": grades_per_term,
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

    await _setup_services(hass)

    return True


# ---------------------------------------------------------------------------
# Services
# ---------------------------------------------------------------------------

MEAL_TYPES = {
    "snack": MealType.SNACK,
    "lunch": MealType.LUNCH,
    "afternoon_snack": MealType.AFTERNOON_SNACK,
}

_services_ready = False


def _resolve_coordinator(hass, call: ServiceCall):
    """Resolve the coordinator targeted by a service call.

    Services are registered ONCE globally (not per config entry) so that
    multiple student/account entries no longer overwrite each other's handlers.
    The optional ``entry_id`` in the call data selects a specific entry;
    otherwise the current (usually single) entry is used. An ``entry_id`` that
    is supplied but not loaded always raises rather than falling back to
    another account.
    """
    entries = hass.data.get(DOMAIN, {})
    entry_id = (call.data or {}).get("entry_id")
    if entry_id and entry_id in entries:
        return entries[entry_id]
    if entry_id and entry_id not in entries:
        raise vol.Invalid(f"EduPage entry_id {entry_id} is not loaded")
    if not entries:
        raise vol.Invalid("No EduPage config entry is loaded")
    if len(entries) > 1 and not entry_id:
        raise vol.Invalid(
            "Multiple EduPage entries are loaded; specify 'entry_id' to choose one"
        )
    return next(iter(entries.values()))


def _find_meal(coordinator, meal_date: str, meal_type: str):
    """Locate a Meal object from the coordinator's cached canteen data."""
    try:
        day = datetime.strptime(meal_date, "%Y-%m-%d").date()
    except (TypeError, ValueError) as e:
        raise vol.Invalid("Invalid date, expected YYYY-MM-DD") from e

    meal_type_enum = MEAL_TYPES.get(meal_type.lower())
    if meal_type_enum is None:
        raise vol.Invalid(
            f"Invalid meal_type, expected one of {', '.join(MEAL_TYPES)}"
        )

    day_meals = coordinator.data.get("canteen_menu", {}).get(day, [])
    for meal in day_meals:
        if meal.meal_type == meal_type_enum:
            return meal
    raise vol.Invalid(
        f"No {meal_type} meal found in the canteen data for {meal_date}"
    )


def _find_menu(meal, menu_number):
    """Locate a Menu (by number) inside a Meal for rating purposes."""
    for menu in meal.menus or []:
        if menu.number == str(menu_number):
            return menu
    raise vol.Invalid(f"Menu number {menu_number} not found for the given meal")


async def _setup_services(hass: HomeAssistant):
    """Register EduPage services once, globally.

    Registration happens a single time (guarded) so that loading a second
    config entry cannot replace the handlers of the first. Each service call
    resolves its target coordinator from the optional ``entry_id``.
    """
    global _services_ready
    if _services_ready:
        return
    _services_ready = True

    async def async_choose_meal(call: ServiceCall):
        coordinator = _resolve_coordinator(hass, call)
        meal = _find_meal(coordinator, call.data.get("date"), call.data.get("meal_type"))
        number = call.data.get("number")
        async with coordinator.fetch_lock:
            await coordinator.edupage.choose_meal(meal, number)
        await coordinator.async_request_refresh()

    async def async_sign_off_meal(call: ServiceCall):
        coordinator = _resolve_coordinator(hass, call)
        meal = _find_meal(coordinator, call.data.get("date"), call.data.get("meal_type"))
        async with coordinator.fetch_lock:
            await coordinator.edupage.sign_off_meal(meal)
        await coordinator.async_request_refresh()

    async def async_rate_meal(call: ServiceCall):
        coordinator = _resolve_coordinator(hass, call)
        meal = _find_meal(coordinator, call.data.get("date"), call.data.get("meal_type"))
        menu = _find_menu(meal, call.data.get("menu_number"))
        if menu.rating is None:
            raise vol.Invalid(
                "This menu has no rating object; it may not be rateable yet"
            )
        quantity = call.data.get("quantity")
        quality = call.data.get("quality")
        async with coordinator.fetch_lock:
            await coordinator.edupage.rate_meal(menu.rating, quantity, quality)
        await coordinator.async_request_refresh()

    async def async_send_message(call: ServiceCall):
        coordinator = _resolve_coordinator(hass, call)
        recipients_raw = call.data.get("recipients")
        body = call.data.get("body")
        if isinstance(recipients_raw, str):
            recipients_raw = [r.strip() for r in recipients_raw.split(",") if r.strip()]
        if not recipients_raw:
            raise vol.Invalid("At least one recipient is required")
        async with coordinator.fetch_lock:
            await coordinator.edupage.send_message(recipients_raw, body)

    entry_opt = {vol.Optional("entry_id"): cv.string}

    hass.services.async_register(
        DOMAIN,
        "choose_meal",
        async_choose_meal,
        vol.Schema(
            {
                **entry_opt,
                vol.Required("date"): cv.string,
                vol.Required("meal_type"): vol.In(list(MEAL_TYPES)),
                vol.Required("number"): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=8)
                ),
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        "sign_off_meal",
        async_sign_off_meal,
        vol.Schema(
            {
                **entry_opt,
                vol.Required("date"): cv.string,
                vol.Required("meal_type"): vol.In(list(MEAL_TYPES)),
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        "rate_meal",
        async_rate_meal,
        vol.Schema(
            {
                **entry_opt,
                vol.Required("date"): cv.string,
                vol.Required("meal_type"): vol.In(list(MEAL_TYPES)),
                vol.Required("menu_number"): cv.string,
                vol.Required("quantity"): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=10)
                ),
                vol.Required("quality"): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=10)
                ),
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        "send_message",
        async_send_message,
        vol.Schema(
            {
                **entry_opt,
                vol.Required("recipients"): cv.ensure_list,
                vol.Required("body"): cv.string,
            }
        ),
    )


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