import logging

from edupage_api import Edupage as APIEdupage
from edupage_api import Login
from edupage_api.exceptions import BadCredentialsException

from homeassistant.helpers.update_coordinator import UpdateFailed

_LOGGER = logging.getLogger(__name__)


class EdupageSessionExpired(UpdateFailed):
    """Raised when the stored PHPSESSID is invalid/expired.

    Subclass of UpdateFailed so the DataUpdateCoordinator still treats it as a
    failed update, but it can be caught separately in __init__ to trigger
    Home Assistant's reauthentication flow.
    """


class Edupage:
    """Async wrapper around the edupage-api library.

    Runtime polling is deliberately session-only: a fresh PHPSESSID obtained
    during the config flow (after 2FA) is reused via `Login.reload_data`.
    We never call `api.login()` at runtime so a 2FA prompt is never re-triggered
    by the 30-minute coordinator poll. If the stored session turns out to be
    expired we signal that with EdupageSessionExpired so the integration can
    start the reauth flow.
    """

    def __init__(self, hass, sessionid=""):
        self.hass = hass
        self.sessionid = sessionid
        self.api = APIEdupage()

    def _load_session(self, subdomain, sessionid, username):
        """Reload a stored EduPage session synchronously."""
        login = Login(self.api)
        login.reload_data(subdomain, sessionid, username)

    async def login(self, username, password, subdomain, sessionid):
        """Load the stored session. Never starts username/password login."""
        self.sessionid = sessionid
        try:
            await self.hass.async_add_executor_job(
                self._load_session, subdomain, sessionid, username
            )
            if not self.api.is_logged_in:
                _LOGGER.error(
                    "EDUPAGE stored session is invalid or expired; "
                    "re-authenticate to refresh it."
                )
                raise EdupageSessionExpired(
                    "EduPage session expired; please re-authenticate."
                )
            _LOGGER.debug("EDUPAGE session loaded for %s@%s", username, subdomain)
            return True
        except BadCredentialsException as e:
            _LOGGER.error(
                "EDUPAGE stored session is invalid or expired: %s. "
                "Re-authenticate to refresh the session.",
                e,
            )
            raise EdupageSessionExpired(
                "EduPage session invalid/expired; please re-authenticate the "
                "integration to refresh it."
            ) from e
        except Exception as e:  # noqa: BLE001
            _LOGGER.error("EDUPAGE unexpected session-load error: %s", e)
            raise UpdateFailed(f"EduPage session load failed: {e}") from e

    async def get_classes(self):
        try:
            return await self.hass.async_add_executor_job(self.api.get_classes)
        except Exception as e:  # noqa: BLE001
            raise UpdateFailed(f"EDUPAGE error updating get_classes() data from API: {e}")

    async def get_grades(self):
        try:
            return await self.hass.async_add_executor_job(self.api.get_grades)
        except Exception as e:  # noqa: BLE001
            raise UpdateFailed(f"EDUPAGE error updating get_grades() data from API: {e}")

    async def get_subjects(self):
        try:
            return await self.hass.async_add_executor_job(self.api.get_subjects)
        except Exception as e:  # noqa: BLE001
            raise UpdateFailed(f"EDUPAGE error updating get_subjects() data from API: {e}")

    async def get_notifications(self):
        try:
            notifications = await self.hass.async_add_executor_job(
                self.api.get_notifications
            )
            _LOGGER.debug("EDUPAGE Notifications found %s", notifications)
            return notifications
        except Exception as e:  # noqa: BLE001
            raise UpdateFailed(
                f"EDUPAGE error updating get_notifications() data from API: {e}"
            )

    async def get_students(self):
        try:
            return await self.hass.async_add_executor_job(self.api.get_students)
        except Exception as e:  # noqa: BLE001
            raise UpdateFailed(
                f"EDUPAGE error updating get_students() data from API: {e}"
            )

    async def get_user_id(self):
        try:
            return await self.hass.async_add_executor_job(self.api.get_user_id)
        except Exception as e:  # noqa: BLE001
            raise UpdateFailed(
                f"EDUPAGE error updating get_user_id() data from API: {e}"
            )

    async def get_classrooms(self):
        try:
            return await self.hass.async_add_executor_job(self.api.get_classrooms)
        except Exception as e:  # noqa: BLE001
            raise UpdateFailed(
                f"EDUPAGE error updating get_classrooms data from API: {e}"
            )

    async def get_teachers(self):
        try:
            return await self.hass.async_add_executor_job(self.api.get_teachers)
        except Exception as e:  # noqa: BLE001
            raise UpdateFailed(
                f"EDUPAGE error updating get_teachers data from API: {e}"
            )

    async def get_timetable(self, edu_student, day):
        try:
            timetable = await self.hass.async_add_executor_job(
                self.api.get_timetable, edu_student, day
            )
            if timetable is None:
                _LOGGER.debug("EDUPAGE timetable is None for %s", day)
            else:
                _LOGGER.debug("EDUPAGE timetable_data for %s: %s", day, timetable)
            return timetable
        except Exception as e:  # noqa: BLE001
            _LOGGER.error(
                "EDUPAGE error updating get_timetable() data for %s: %s", day, e
            )
            raise UpdateFailed(
                f"EDUPAGE error updating get_timetable() data for {day}: {e}"
            )

    async def get_meals(self, day):
        try:
            meals = await self.hass.async_add_executor_job(self.api.get_meals, day)
            if meals is None:
                _LOGGER.debug("EDUPAGE meals is None for %s", day)
            else:
                _LOGGER.debug("EDUPAGE meals for %s: %s", day, meals)
            return meals
        except Exception as e:  # noqa: BLE001
            _LOGGER.error("EDUPAGE error updating get_meals() data for %s: %s", day, e)
            raise UpdateFailed(
                f"EDUPAGE error updating get_meals() data for {day}: {e}"
            )