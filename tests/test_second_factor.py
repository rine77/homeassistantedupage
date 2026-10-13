"""Tests for the second-factor step, shared by initial setup and reauth.

edupage-api 0.13.3 drives the modern EduPage second-factor flow entirely
through ``TwoFactorLogin``: a typed code, a code emailed on request, a resent
app notification, or an approval already given in the app. EduPage rate-limits
the middle two with a countdown that arrives as ``RetryLaterException``.

These tests cover each path, the cooldown, and the masking of the email address
before it is shown in the form.
"""

from unittest.mock import MagicMock, patch

import pytest

from homeassistant import config_entries
from homeassistant.core import HomeAssistant

from edupage_api.exceptions import (
    RequestError,
    RetryLaterException,
    SecondFactorFailedException,
)

from custom_components.homeassistantedupage.config_flow import _mask_email
from custom_components.homeassistantedupage.const import DOMAIN


def _entry_data():
    return {"username": "user", "password": "pw", "subdomain": "school"}


def _student():
    s = MagicMock()
    s.person_id = 123
    s.name = "Max Kovaľ"
    return [s]


def _challenge(**kwargs):
    """A TwoFactorLogin stand-in with the modern-flow attributes set."""
    challenge = MagicMock()
    challenge.email = "janko.koval@example.com"
    challenge.device_names = ["iPhone", "iPad"]
    for name, value in kwargs.items():
        setattr(challenge, name, value)
    return challenge


def _api(challenge, logged_in=True):
    api = MagicMock()
    api.is_logged_in = logged_in
    api.login.return_value = challenge
    api.session.cookies.get_dict.return_value = {"PHPSESSID": "phpsess_123"}
    api.get_students.return_value = _student()
    return api


async def _start(hass, challenge, logged_in=True):
    """Run the flow up to the second-factor step and return (flow_id, api)."""
    api = _api(challenge, logged_in)
    with patch(
        "custom_components.homeassistantedupage.config_flow.Edupage",
        return_value=api,
    ):
        init = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            init["flow_id"], user_input=_entry_data()
        )
    assert result["step_id"] == "two_factor"
    return init["flow_id"], api


# ---------------------------------------------------------------------------
# mask_email
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("janko.koval@example.com", "j***@example.com"),
        ("a@b.cz", "a***@b.cz"),
        ("@example.com", "***@example.com"),
        ("not-an-email", ""),
        (None, ""),
        ("", ""),
    ],
)
def test_mask_email(raw, expected):
    assert _mask_email(raw) == expected


# ---------------------------------------------------------------------------
# the form itself
# ---------------------------------------------------------------------------


async def test_step_offers_all_four_options(hass: HomeAssistant):
    """The form carries the code plus the three one-shot actions."""
    flow_id, _ = await _start(hass, _challenge())

    with patch(
        "custom_components.homeassistantedupage.config_flow.Edupage",
        return_value=_api(_challenge()),
    ):
        result = await hass.config_entries.flow.async_configure(flow_id, user_input={})

    keys = {str(k) for k in result["data_schema"].schema}
    assert keys == {
        "two_factor_code",
        "send_email_code",
        "resend_notification",
        "device_confirmed",
    }


async def test_form_shows_masked_email_and_devices(hass: HomeAssistant):
    """The address is masked and the paired devices are listed."""
    flow_id, _ = await _start(hass, _challenge())

    result = await hass.config_entries.flow.async_configure(flow_id, user_input={})
    placeholders = result["description_placeholders"]

    assert placeholders["email"] == "j***@example.com"
    assert placeholders["devices"] == "iPhone, iPad"
    assert placeholders["retry_seconds"] == 0


# ---------------------------------------------------------------------------
# typed code
# ---------------------------------------------------------------------------


async def test_correct_code_completes_setup(hass: HomeAssistant):
    challenge = _challenge()
    flow_id, _ = await _start(hass, challenge)

    with patch(
        "custom_components.homeassistantedupage.config_flow.Edupage",
        return_value=_api(challenge),
    ):
        result = await hass.config_entries.flow.async_configure(
            flow_id, user_input={"two_factor_code": "123456"}
        )

    challenge.finish_with_code.assert_called_once_with("123456")
    assert result["step_id"] == "select_student"


async def test_code_is_stripped_before_submission(hass: HomeAssistant):
    challenge = _challenge()
    flow_id, _ = await _start(hass, challenge)

    with patch(
        "custom_components.homeassistantedupage.config_flow.Edupage",
        return_value=_api(challenge),
    ):
        await hass.config_entries.flow.async_configure(
            flow_id, user_input={"two_factor_code": "  123456  "}
        )

    challenge.finish_with_code.assert_called_once_with("123456")


async def test_wrong_code_reports_invalid_code(hass: HomeAssistant):
    challenge = _challenge()
    challenge.finish_with_code.side_effect = SecondFactorFailedException("nope")
    flow_id, _ = await _start(hass, challenge)

    with patch(
        "custom_components.homeassistantedupage.config_flow.Edupage",
        return_value=_api(challenge),
    ):
        result = await hass.config_entries.flow.async_configure(
            flow_id, user_input={"two_factor_code": "000000"}
        )

    assert result["errors"] == {"base": "invalid_code"}


async def test_code_accepted_without_session_is_invalid(hass: HomeAssistant):
    """EduPage accepted the code but never handed back a usable session."""
    challenge = _challenge()
    flow_id, _ = await _start(hass, challenge, logged_in=False)

    with patch(
        "custom_components.homeassistantedupage.config_flow.Edupage",
        return_value=_api(challenge, logged_in=False),
    ):
        result = await hass.config_entries.flow.async_configure(
            flow_id, user_input={"two_factor_code": "123456"}
        )

    assert result["errors"] == {"base": "invalid_code"}


async def test_empty_submission_reports_invalid_code(hass: HomeAssistant):
    """Submitting with no code and no action ticked is a no-op."""
    challenge = _challenge()
    flow_id, _ = await _start(hass, challenge)

    with patch(
        "custom_components.homeassistantedupage.config_flow.Edupage",
        return_value=_api(challenge),
    ):
        result = await hass.config_entries.flow.async_configure(
            flow_id, user_input={"two_factor_code": "   "}
        )

    challenge.finish_with_code.assert_not_called()
    assert result["errors"] == {"base": "invalid_code"}


# ---------------------------------------------------------------------------
# email code
# ---------------------------------------------------------------------------


async def test_send_email_code_asks_the_api_and_keeps_the_step(hass: HomeAssistant):
    challenge = _challenge()
    flow_id, _ = await _start(hass, challenge)

    with patch(
        "custom_components.homeassistantedupage.config_flow.Edupage",
        return_value=_api(challenge),
    ):
        result = await hass.config_entries.flow.async_configure(
            flow_id, user_input={"send_email_code": True}
        )

    challenge.send_email_code.assert_called_once_with()
    challenge.finish_with_code.assert_not_called()
    assert result["step_id"] == "two_factor"
    assert not result["errors"]


async def test_email_code_through_the_api_completes_setup(hass: HomeAssistant):
    """The emailed code is entered on the same form and finishes the login."""
    challenge = _challenge()
    flow_id, _ = await _start(hass, challenge)

    with patch(
        "custom_components.homeassistantedupage.config_flow.Edupage",
        return_value=_api(challenge),
    ):
        await hass.config_entries.flow.async_configure(
            flow_id, user_input={"send_email_code": True}
        )
        result = await hass.config_entries.flow.async_configure(
            flow_id, user_input={"two_factor_code": "654321"}
        )

    challenge.finish_with_code.assert_called_once_with("654321")
    assert result["step_id"] == "select_student"


async def test_email_request_failure_does_not_end_the_flow(hass: HomeAssistant):
    challenge = _challenge()
    challenge.send_email_code.side_effect = RequestError("nope")
    flow_id, _ = await _start(hass, challenge)

    with patch(
        "custom_components.homeassistantedupage.config_flow.Edupage",
        return_value=_api(challenge),
    ):
        result = await hass.config_entries.flow.async_configure(
            flow_id, user_input={"send_email_code": True}
        )

    assert result["step_id"] == "two_factor"
    assert result["errors"] == {"base": "cannot_connect"}


# ---------------------------------------------------------------------------
# resend notification
# ---------------------------------------------------------------------------


async def test_resend_notification_asks_the_api(hass: HomeAssistant):
    challenge = _challenge()
    flow_id, _ = await _start(hass, challenge)

    with patch(
        "custom_components.homeassistantedupage.config_flow.Edupage",
        return_value=_api(challenge),
    ):
        result = await hass.config_entries.flow.async_configure(
            flow_id, user_input={"resend_notification": True}
        )

    challenge.resend_notifications.assert_called_once_with()
    assert result["step_id"] == "two_factor"
    assert not result["errors"]


# ---------------------------------------------------------------------------
# device confirmation
# ---------------------------------------------------------------------------


async def test_device_confirmed_finishes_without_a_code(hass: HomeAssistant):
    challenge = _challenge()
    challenge.is_confirmed.return_value = True
    flow_id, _ = await _start(hass, challenge)

    with patch(
        "custom_components.homeassistantedupage.config_flow.Edupage",
        return_value=_api(challenge),
    ):
        result = await hass.config_entries.flow.async_configure(
            flow_id, user_input={"device_confirmed": True}
        )

    challenge.is_confirmed.assert_called_once_with()
    challenge.finish.assert_called_once_with()
    challenge.finish_with_code.assert_not_called()
    assert result["step_id"] == "select_student"


async def test_device_not_confirmed_yet_asks_the_user_to_retry(hass: HomeAssistant):
    challenge = _challenge()
    challenge.is_confirmed.return_value = False
    flow_id, _ = await _start(hass, challenge)

    with patch(
        "custom_components.homeassistantedupage.config_flow.Edupage",
        return_value=_api(challenge),
    ):
        result = await hass.config_entries.flow.async_configure(
            flow_id, user_input={"device_confirmed": True}
        )

    challenge.finish.assert_not_called()
    assert result["errors"] == {"base": "not_confirmed"}


async def test_device_confirmed_without_session_is_invalid(hass: HomeAssistant):
    """Approved in the app, yet EduPage handed back no usable session."""
    challenge = _challenge()
    challenge.is_confirmed.return_value = True
    flow_id, _ = await _start(hass, challenge, logged_in=False)

    with patch(
        "custom_components.homeassistantedupage.config_flow.Edupage",
        return_value=_api(challenge, logged_in=False),
    ):
        result = await hass.config_entries.flow.async_configure(
            flow_id, user_input={"device_confirmed": True}
        )

    assert result["errors"] == {"base": "invalid_code"}


# ---------------------------------------------------------------------------
# EduPage's request cooldown
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("action", "method"),
    [
        ("send_email_code", "send_email_code"),
        # The form key is singular, the TwoFactorLogin method plural.
        ("resend_notification", "resend_notifications"),
    ],
)
async def test_retry_later_starts_a_countdown(hass: HomeAssistant, action, method):
    challenge = _challenge()
    getattr(challenge, method).side_effect = RetryLaterException("wait", 42)
    flow_id, _ = await _start(hass, challenge)

    with patch(
        "custom_components.homeassistantedupage.config_flow.Edupage",
        return_value=_api(challenge),
    ):
        result = await hass.config_entries.flow.async_configure(
            flow_id, user_input={action: True}
        )

    assert result["errors"] == {}
    assert result["step_id"] == "two_factor"
    # The countdown is shown to the user rather than surfacing as a failure.
    remaining = result["description_placeholders"]["retry_seconds"]
    assert 40 <= remaining <= 42


async def test_countdown_expires_by_itself(hass: HomeAssistant):
    """A retry_later of zero seconds leaves no active cooldown."""
    challenge = _challenge()
    challenge.send_email_code.side_effect = RetryLaterException("wait", 0)
    flow_id, _ = await _start(hass, challenge)

    with patch(
        "custom_components.homeassistantedupage.config_flow.Edupage",
        return_value=_api(challenge),
    ):
        result = await hass.config_entries.flow.async_configure(
            flow_id, user_input={"send_email_code": True}
        )

    assert result["description_placeholders"]["retry_seconds"] == 0


async def test_countdown_does_not_block_a_typed_code(hass: HomeAssistant):
    """The cooldown only rate-limits resend/email, not code submission."""
    challenge = _challenge()
    challenge.send_email_code.side_effect = RetryLaterException("wait", 60)
    flow_id, _ = await _start(hass, challenge)

    with patch(
        "custom_components.homeassistantedupage.config_flow.Edupage",
        return_value=_api(challenge),
    ):
        await hass.config_entries.flow.async_configure(
            flow_id, user_input={"send_email_code": True}
        )
        result = await hass.config_entries.flow.async_configure(
            flow_id, user_input={"two_factor_code": "123456"}
        )

    challenge.finish_with_code.assert_called_once_with("123456")
    assert result["step_id"] == "select_student"


# ---------------------------------------------------------------------------
# action precedence
# ---------------------------------------------------------------------------


async def test_actions_are_not_confused_with_a_typed_code(hass: HomeAssistant):
    """Ticking an action and filling the code runs the action, not the code."""
    challenge = _challenge()
    flow_id, _ = await _start(hass, challenge)

    with patch(
        "custom_components.homeassistantedupage.config_flow.Edupage",
        return_value=_api(challenge),
    ):
        await hass.config_entries.flow.async_configure(
            flow_id,
            user_input={"two_factor_code": "123456", "resend_notification": True},
        )

    challenge.resend_notifications.assert_called_once_with()
    challenge.finish_with_code.assert_not_called()