import logging
from datetime import timedelta

import voluptuous as vol

from edupage_api import Edupage
from edupage_api.exceptions import (
    BadCredentialsException,
    CaptchaException,
    RequestError,
    RetryLaterException,
    SecondFactorFailedException,
)

from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.util import dt as dt_util

from .const import (
    CONF_PHPSESSID,
    CONF_STUDENT_ID,
    CONF_STUDENT_NAME,
    CONF_SUBDOMAIN,
    CONF_SUBJECT_IDS,
    CONF_TWO_FACTOR_CODE,
    DOMAIN,
    ISSUE_TRACKER,
)

_LOGGER = logging.getLogger(__name__)

# One-shot actions offered on the second-factor step. Each is a boolean that is
# ticked together with an (optional) code, performs its action, and re-renders
# the step without consuming it.
CONF_SEND_EMAIL_CODE = "send_email_code"
# Note: the form key is singular but the TwoFactorLogin method is plural
# (`resend_notifications`).
CONF_RESEND_NOTIFICATION = "resend_notification"
CONF_DEVICE_CONFIRMED = "device_confirmed"


def _mask_email(value):
    """Mask an email address for display, e.g. `j***@example.com`."""
    if not value or "@" not in value:
        return ""
    local, _, domain = value.partition("@")
    return f"{local[:1]}***@{domain}" if local else f"***@{domain}"


class EdupageConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Edupage (with optional TOTP 2FA)."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(
        _config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Return the options flow handler."""
        return EdupageOptionsFlow()

    def __init__(self) -> None:
        """Initialise the flow."""
        self._reauth = False
        self._challenge = None
        self._challenge_api = None
        self._cooldown_until = None

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
                    # `edupage-api` served a two-factor page it could not parse.
                    # Nothing the flow can do here: retrying with the same
                    # credentials lands on the same page.
                    _LOGGER.error(
                        "EduPage served a two-factor page that edupage-api could "
                        "not parse. Please report it at %s",
                        ISSUE_TRACKER,
                    )
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
                self._challenge_api = api
                return await self.async_step_two_factor()
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
                self._challenge = second_factor
                self._challenge_api = api
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

    async def async_step_two_factor(self, user_input=None):
        """Collect the second factor for a new account."""

        async def _done(api):
            return await self._finalize_setup(api, self.user_input)

        return await self._async_second_factor(
            "two_factor", user_input, self._challenge, self._challenge_api, _done
        )

    async def _async_second_factor(self, step_id, user_input, challenge, api, on_done):
        """Render and process the shared second-factor step.

        The step offers four ways forward, each submitted with the same form:
        enter a code, have the code emailed, resend the app notification, or
        report that the login was confirmed in the app.
        """
        errors = {}

        if challenge is None or api is None:
            errors["base"] = "cannot_connect"
        elif user_input is not None:
            code = (user_input.get(CONF_TWO_FACTOR_CODE) or "").strip()
            try:
                if user_input.get(CONF_SEND_EMAIL_CODE) or user_input.get(
                    CONF_RESEND_NOTIFICATION
                ):
                    # EduPage rate-limits code requests. Honour its countdown
                    # instead of firing a request it will only refuse, which
                    # would restart the timer.
                    remaining = self._cooldown_remaining()
                    if remaining:
                        _LOGGER.debug(
                            "EduPage second-factor request suppressed; %s s left",
                            remaining,
                        )
                        errors["base"] = "code_request_too_soon"
                    elif user_input.get(CONF_SEND_EMAIL_CODE):
                        await self.hass.async_add_executor_job(
                            challenge.send_email_code
                        )
                        _LOGGER.debug("EduPage second-factor code sent by email")
                    else:
                        await self.hass.async_add_executor_job(
                            challenge.resend_notifications
                        )
                        _LOGGER.debug("EduPage second-factor notification resent")
                elif user_input.get(CONF_DEVICE_CONFIRMED):
                    confirmed = await self.hass.async_add_executor_job(
                        challenge.is_confirmed
                    )
                    if not confirmed:
                        errors["base"] = "not_confirmed"
                    else:
                        await self.hass.async_add_executor_job(challenge.finish)
                        if not api.is_logged_in:
                            errors["base"] = "invalid_code"
                        else:
                            return await on_done(api)
                elif code:
                    await self.hass.async_add_executor_job(
                        challenge.finish_with_code, code
                    )
                    if not api.is_logged_in:
                        errors["base"] = "invalid_code"
                    else:
                        return await on_done(api)
                else:
                    errors["base"] = "invalid_code"
            except SecondFactorFailedException as e:
                _LOGGER.debug("EduPage second factor rejected: %s", e)
                errors["base"] = "invalid_code"
            except RetryLaterException as e:
                # EduPage rate-limits resend/email requests with a countdown.
                _LOGGER.debug(
                    "EduPage second-factor request too soon, retry in %s s",
                    e.retry_in_seconds,
                )
                self._set_second_factor_cooldown(e.retry_in_seconds)
            except RequestError as e:
                _LOGGER.debug("EduPage second-factor request failed: %s", e)
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error during EduPage second factor")
                errors["base"] = "cannot_connect"

        return self._second_factor_form(step_id, errors, challenge)

    def _set_second_factor_cooldown(self, seconds):
        """Remember how long EduPage wants us to wait before the next request."""
        self._cooldown_until = dt_util.utcnow() + timedelta(
            seconds=max(0, int(seconds or 0))
        )

    def _cooldown_remaining(self):
        """Seconds left on the resend/email cooldown, or 0 when it has expired."""
        remaining = getattr(self, "_cooldown_until", None)
        if remaining is None:
            return 0
        return max(0, int((remaining - dt_util.utcnow()).total_seconds()))

    def _second_factor_form(self, step_id, errors, challenge):
        """Build the second-factor form, including countdown and masked email."""
        placeholders = {
            "email": _mask_email(getattr(challenge, "email", None)),
            "devices": ", ".join(getattr(challenge, "device_names", None) or []),
            "retry_seconds": self._cooldown_remaining(),
        }
        schema = vol.Schema(
            {
                vol.Optional(CONF_TWO_FACTOR_CODE, default=""): str,
                vol.Optional(CONF_SEND_EMAIL_CODE, default=False): bool,
                vol.Optional(CONF_RESEND_NOTIFICATION, default=False): bool,
                vol.Optional(CONF_DEVICE_CONFIRMED, default=False): bool,
            }
        )
        return self.async_show_form(
            step_id=step_id,
            data_schema=schema,
            errors=errors,
            description_placeholders=placeholders,
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
        # Persist only what is needed for later authentication: the account
        # identity and the freshly-obtained session. The password is never
        # stored in the config entry.
        self.user_data = {
            CONF_USERNAME: user_input[CONF_USERNAME],
            CONF_SUBDOMAIN: user_input[CONF_SUBDOMAIN],
            CONF_PHPSESSID: phpsess,
        }
        self.students = {
            student.person_id: student.name or str(student.person_id)
            for student in students
        }
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

        Reuses the reconfigure flow, which prompts for the password, re-logs-in
        (with a 2FA code prompt if the account has 2FA enabled), stores a fresh
        PHPSESSID and reloads the config entry. Completed as a reauthentication.
        """
        self._reauth = True
        return await self.async_step_reconfigure(user_input)

    async def async_step_reconfigure(self, user_input=None):
        """Re-login to refresh an expired stored session.

        The password is not stored in the config entry, so it is prompted for
        fresh on each reauthentication. Username and subdomain are pre-filled
        from the existing entry.
        """
        if not self._reauth:
            self._reauth = False
        entry = self._get_reconfigure_entry()
        if entry is None:
            return self.async_abort(reason="already_configured")

        self._reconfig_entry = entry
        errors = {}
        second_factor = None

        if user_input is not None:
            username = user_input.get(CONF_USERNAME) or entry.data[CONF_USERNAME]
            password = user_input[CONF_PASSWORD]
            subdomain = user_input.get(CONF_SUBDOMAIN) or entry.data[CONF_SUBDOMAIN]

            api = Edupage()
            try:
                second_factor = await self.hass.async_add_executor_job(
                    api.login,
                    username,
                    password,
                    subdomain,
                )
            except CaptchaException as e:
                _LOGGER.debug(
                    "EduPage re-login captcha for %s@%s: %r",
                    username,
                    subdomain,
                    e,
                )
                errors["base"] = "captcha_required"
            except BadCredentialsException as e:
                _LOGGER.debug(
                    "EduPage re-login rejected for %s@%s: %r",
                    username,
                    subdomain,
                    e,
                )
                if "two-factor fields" in str(e):
                    _LOGGER.error(
                        "EduPage served a two-factor page that edupage-api could "
                        "not parse. Please report it at %s",
                        ISSUE_TRACKER,
                    )
                else:
                    _LOGGER.error(
                        "EduPage re-login failed: invalid username or password."
                    )
                errors["base"] = "invalid_auth"
            except SecondFactorFailedException as e:
                _LOGGER.debug(
                    "EduPage re-login SecondFactorFailed for %s@%s: %r",
                    username,
                    subdomain,
                    e,
                )
                errors["base"] = "invalid_auth"
            except Exception as e:  # noqa: BLE001
                _LOGGER.debug(
                    "EduPage re-login unexpected error for %s@%s: %r",
                    username,
                    subdomain,
                    e,
                )
                _LOGGER.exception("Unexpected error during EduPage re-login")
                errors["base"] = "cannot_connect"

            if not errors and second_factor is not None:
                # 2FA required: capture the object, ask for the confirmation code.
                self._challenge = second_factor
                self._challenge_api = api
                return await self.async_step_reconfigure_code()

            if not errors:
                return await self._apply_reconfigure(api)

            if self._reauth:
                return self.async_abort(
                    reason="reauth_unsuccessful",
                    description_placeholders={"error": errors.get("base", "cannot_connect")},
                )

        data_schema = vol.Schema(
            {
                vol.Required(
                    CONF_USERNAME,
                    default=entry.data[CONF_USERNAME],
                ): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Required(
                    CONF_SUBDOMAIN,
                    default=entry.data[CONF_SUBDOMAIN],
                ): str,
            }
        )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=data_schema,
            errors=errors,
        )

    async def async_step_reconfigure_code(self, user_input=None):
        """Enter the second factor when reauthenticating."""

        async def _done(api):
            return await self._apply_reconfigure(api)

        return await self._async_second_factor(
            "reconfigure_code",
            user_input,
            getattr(self, "_challenge", None),
            getattr(self, "_challenge_api", None),
            _done,
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


class EdupageOptionsFlow(config_entries.OptionsFlow):
    """Handle EduPage integration options."""

    async def async_step_init(self, user_input=None):
        """Select subjects for which grade sensors are created."""
        coordinator = self.hass.data.get(DOMAIN, {}).get(
            self.config_entry.entry_id
        )
        subjects = (
            coordinator.data.get("subjects", [])
            if coordinator is not None and coordinator.data
            else []
        )

        subject_options = [
            {
                "value": str(subject.subject_id),
                "label": subject.name,
            }
            for subject in sorted(
                subjects,
                key=lambda subject: subject.name.casefold(),
            )
        ]

        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        available_subject_ids = [
            option["value"] for option in subject_options
        ]
        selected_subject_ids = self.config_entry.options.get(
            CONF_SUBJECT_IDS,
            available_subject_ids,
        )
        selected_subject_ids = [
            str(subject_id)
            for subject_id in selected_subject_ids
            if str(subject_id) in available_subject_ids
        ]

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SUBJECT_IDS,
                        default=selected_subject_ids,
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=subject_options,
                            multiple=True,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
        )
