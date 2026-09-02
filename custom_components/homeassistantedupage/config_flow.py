import logging
import voluptuous as vol

from edupage_api import Edupage
from edupage_api.exceptions import (
    BadCredentialsException,
    CaptchaException,
    SecondFactorFailedException,
)

from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback

from .const import (
    CONF_PHPSESSID,
    CONF_STUDENT_ID,
    CONF_STUDENT_NAME,
    CONF_SUBDOMAIN,
    CONF_TWO_FACTOR_CODE,
    DOMAIN,
)
from .twofactor import start_two_factor

_LOGGER = logging.getLogger(__name__)


class EdupageConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Edupage (with optional TOTP 2FA)."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise the flow."""
        self._reauth = False

    async def async_step_user(self, user_input=None):
        """Handle the initial step: username / password / subdomain."""
        errors = {}
        if user_input is not None:
            self.user_input = user_input
            api = Edupage()
            second_factor = None
            try:
                second_factor = await self.hass.async_add_executor_job(
                    api.login,
                    user_input[CONF_USERNAME],
                    user_input[CONF_PASSWORD],
                    user_input[CONF_SUBDOMAIN],
                )
            except BadCredentialsException as e:
                _LOGGER.debug(
                    "EduPage login for %s@%s returned bad credentials: %s",
                    user_input.get(CONF_USERNAME),
                    user_input.get(CONF_SUBDOMAIN),
                    e,
                )
                if "two-factor fields" in str(e):
                    # Modern EduPage 2FA page: `edupage-api` can no longer parse
                    # the challenge from hidden inputs. Drive the app-code flow
                    # ourselves so the existing TOTP step can be shown.
                    try:
                        second_factor = await self.hass.async_add_executor_job(
                            start_two_factor,
                            api,
                            user_input[CONF_USERNAME],
                            user_input[CONF_PASSWORD],
                            user_input[CONF_SUBDOMAIN],
                        )
                    except BadCredentialsException as e2:
                        _LOGGER.error(
                            "EduPage login failed: could not initialise second "
                            "factor authentication for the configured account."
                        )
                        _LOGGER.debug(
                            "EduPage 2FA init failed for %s@%s: %s",
                            user_input.get(CONF_USERNAME),
                            user_input.get(CONF_SUBDOMAIN),
                            e2,
                        )
                        errors["base"] = "invalid_auth"
                else:
                    _LOGGER.error(
                        "EduPage login failed: invalid username or password."
                    )
                    errors["base"] = "invalid_auth"
            except CaptchaException as e:
                _LOGGER.debug(
                    "EduPage login captcha for %s@%s: %s",
                    user_input.get(CONF_USERNAME),
                    user_input.get(CONF_SUBDOMAIN),
                    e,
                )
                errors["base"] = "captcha_required"
            except SecondFactorFailedException as e:
                _LOGGER.debug(
                    "EduPage login second-factor failed for %s@%s: %s",
                    user_input.get(CONF_USERNAME),
                    user_input.get(CONF_SUBDOMAIN),
                    e,
                )
                self.api = api
                return await self.async_step_two_factor(retry=True)
            except Exception as e:  # noqa: BLE001
                _LOGGER.debug(
                    "EduPage login unexpected error for %s@%s: %s",
                    user_input.get(CONF_USERNAME),
                    user_input.get(CONF_SUBDOMAIN),
                    e,
                )
                _LOGGER.exception("Unexpected error during EduPage login")
                errors["base"] = "cannot_connect"

            if not errors and second_factor is not None:
                # 2FA required: capture the object, ask for the confirmation code.
                self.api = api
                self.second_factor = second_factor
                return await self.async_step_two_factor()
            if not errors:
                return await self._finalize_setup(api, user_input)

        data_schema = vol.Schema(
            {
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Required(CONF_SUBDOMAIN): str,
            }
        )
        return self.async_show_form(
            step_id="user", data_schema=data_schema, errors=errors
        )

    async def async_step_two_factor(self, user_input=None, retry=False):
        """Collect the TOTP/confirmation code from the EduPage mobile app."""
        errors = {}
        if user_input is not None:
            second_factor = getattr(self, "second_factor", None)
            if second_factor is None:
                errors["base"] = "cannot_connect"
            else:
                code = user_input[CONF_TWO_FACTOR_CODE].strip()
                try:
                    await self.hass.async_add_executor_job(
                        second_factor.finish_with_code, code
                    )
                    api = self.api
                    if not api.is_logged_in:
                        errors["base"] = "invalid_code"
                    else:
                        return await self._finalize_setup(api, self.user_input)
                except SecondFactorFailedException:
                    errors["base"] = "invalid_code"
                except Exception:  # noqa: BLE001
                    _LOGGER.exception("Unexpected error completing 2FA code")
                    errors["base"] = "cannot_connect"

        schema = vol.Schema({vol.Required(CONF_TWO_FACTOR_CODE): str})
        return self.async_show_form(
            step_id="two_factor",
            data_schema=schema,
            errors=errors,
            description_placeholders={"hint": "Enter the confirmation code from the EduPage app."},
        )

    async def _finalize_setup(self, api, user_input):
        """Fetch students and advance to the student-selection step."""
        try:
            students = await self.hass.async_add_executor_job(api.get_students)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Failed to fetch students after login")
            return self.async_abort(reason="cannot_connect")

        students = students or []
        if not students:
            return self.async_abort(reason="no_students_found")

        cookies = api.session.cookies.get_dict()
        phpsess = cookies.get("PHPSESSID")
        user_input[CONF_PHPSESSID] = phpsess
        self.user_data = user_input
        self.students = {student.person_id: student.name for student in students}
        return await self.async_step_select_student()

    async def async_step_select_student(self, user_input=None):
        """Handle the selection of a student."""
        errors = {}
        if user_input is not None:
            student_id = user_input.get("student")
            return self.async_create_entry(
                title=f"Edupage ({self.students[student_id]})",
                data={
                    **self.user_data,
                    CONF_STUDENT_ID: student_id,
                    CONF_STUDENT_NAME: self.students[student_id],
                },
            )

        student_schema = vol.Schema(
            {vol.Required("student"): vol.In(self.students)}
        )
        return self.async_show_form(
            step_id="select_student",
            data_schema=student_schema,
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Reconfigure flow (for when a stored session expires)
    # ------------------------------------------------------------------

    async def async_step_reauth(self, user_input=None):
        """Triggered when Home Assistant detects an expired/invalid stored session.

        Reuses the reconfigure flow, which re-logs-in (prompting for a 2FA code
        if the account has 2FA enabled), stores a fresh PHPSESSID and reloads
        the config entry. Completed as a reauthentication so the user is not
        asked to re-enter their credentials.
        """
        self._reauth = True
        return await self.async_step_reconfigure(user_input)

    async def async_step_reconfigure(self, user_input=None):
        """Re-login (with 2FA if required) to refresh an expired stored session."""
        if not self._reauth:
            self._reauth = False
        entry = self._get_reconfigure_entry()
        if entry is None:
            return self.async_abort(reason="already_configured")

        if user_input is not None:
            # Only reached when a code step was shown; combine with login below.
            pass

        api = Edupage()
        self._reconfig_entry = entry
        errors = {}
        second_factor = None
        try:
            second_factor = await self.hass.async_add_executor_job(
                api.login,
                entry.data[CONF_USERNAME],
                entry.data[CONF_PASSWORD],
                entry.data[CONF_SUBDOMAIN],
            )
        except CaptchaException as e:
            _LOGGER.debug(
                "EduPage re-login captcha for %s@%s: %r",
                entry.data.get(CONF_USERNAME),
                entry.data.get(CONF_SUBDOMAIN),
                e,
            )
            errors["base"] = "captcha_required"
        except BadCredentialsException as e:
            _LOGGER.debug(
                "EduPage re-login rejected for %s@%s: %r",
                entry.data.get(CONF_USERNAME),
                entry.data.get(CONF_SUBDOMAIN),
                e,
            )
            if "two-factor fields" in str(e):
                # Modern 2FA page: `edupage-api` can no longer parse the
                # challenge from hidden inputs. Drive the app-code flow
                # ourselves so the existing TOTP step can be shown.
                try:
                    second_factor = await self.hass.async_add_executor_job(
                        start_two_factor,
                        api,
                        entry.data[CONF_USERNAME],
                        entry.data[CONF_PASSWORD],
                        entry.data[CONF_SUBDOMAIN],
                    )
                except BadCredentialsException as e2:
                    _LOGGER.error(
                        "EduPage re-login failed: could not initialise second "
                        "factor authentication for the configured account."
                    )
                    _LOGGER.debug(
                        "EduPage 2FA init failed for %s@%s: %r",
                        entry.data.get(CONF_USERNAME),
                        entry.data.get(CONF_SUBDOMAIN),
                        e2,
                    )
                    errors["base"] = "invalid_auth"
            else:
                _LOGGER.error("EduPage re-login failed: invalid username or password.")
                errors["base"] = "invalid_auth"
        except SecondFactorFailedException as e:
            _LOGGER.debug(
                "EduPage re-login SecondFactorFailed for %s@%s: %r",
                entry.data.get(CONF_USERNAME),
                entry.data.get(CONF_SUBDOMAIN),
                e,
            )
            errors["base"] = "invalid_auth"
        except Exception as e:  # noqa: BLE001
            _LOGGER.debug(
                "EduPage re-login unexpected error for %s@%s: %r",
                entry.data.get(CONF_USERNAME),
                entry.data.get(CONF_SUBDOMAIN),
                e,
            )
            _LOGGER.exception("Unexpected error during EduPage re-login")
            errors["base"] = "cannot_connect"

        if not errors and second_factor is not None:
            # 2FA required: capture the object, ask for the confirmation code.
            self.reconfig_api = api
            self.reconfig_second_factor = second_factor
            return await self.async_step_reconfigure_code(user_input)

        if not errors:
            return await self._apply_reconfigure(api)

        return self.async_abort(reason="reconfigure_failed", description_placeholders={"error": errors.get("base", "cannot_connect")})

    async def async_step_reconfigure_code(self, user_input=None):
        """Enter the confirmation code for reauthentication."""
        errors = {}
        if user_input is not None:
            code = user_input[CONF_TWO_FACTOR_CODE].strip()
            api = self.reconfig_api
            try:
                await self.hass.async_add_executor_job(
                    self.reconfig_second_factor.finish_with_code, code
                )
                if api.is_logged_in:
                    return await self._apply_reconfigure(api)
                errors["base"] = "invalid_code"
            except SecondFactorFailedException:
                errors["base"] = "invalid_code"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error completing 2FA code")
                errors["base"] = "cannot_connect"

        schema = vol.Schema({vol.Required(CONF_TWO_FACTOR_CODE): str})
        return self.async_show_form(
            step_id="reconfigure_code",
            data_schema=schema,
            errors=errors,
        )

    async def _apply_reconfigure(self, api):
        """Update the entry with a freshly-obtained PHPSESSID and reload it."""
        entry = self._reconfig_entry
        if entry is None:
            return self.async_abort(reason="already_configured")
        cookies = api.session.cookies.get_dict()
        phpsess = cookies.get("PHPSESSID")
        if not phpsess:
            # A failed or incomplete login must never be reported as a
            # successful reauthentication.
            _LOGGER.error("EduPage re-login did not yield a session.")
            return self.async_show_form(
                step_id="reconfigure",
                errors={"base": "cannot_connect"},
            )
        reason = "reauth_successful" if self._reauth else "reconfigured"
        return self.async_update_reload_and_abort(
            entry,
            data_updates={CONF_PHPSESSID: phpsess},
            reason=reason,
        )

    @callback
    def _get_reconfigure_entry(self):
        for entry in self._async_current_entries():
            if entry.entry_id == self.context.get("entry_id"):
                return entry
        return None
