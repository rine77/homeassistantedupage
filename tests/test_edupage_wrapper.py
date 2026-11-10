"""Regression tests for the EduPage session-expiration handling.

When the stored PHPSESSID is expired, ``Edupage.login()`` must surface an
``EdupageSessionExpired`` (not a generic ``UpdateFailed``) so the integration's
reauthentication flow is started. Covers the two real-world cases from
homeassistantedupage#70 / #95:

* ``LoginSession.reload_data()`` completes but the api reports ``is_logged_in is False``;
* ``LoginSession.reload_data()`` raises ``IndexError``.
"""

from unittest.mock import MagicMock, patch

import pytest
from custom_components.homeassistantedupage.homeassistant_edupage import (
    Edupage,
    EdupageSessionExpired,
)
from edupage_api.exceptions import InsufficientPermissionsException
from homeassistant.helpers.update_coordinator import UpdateFailed


class _FakeHass:
    """Minimal hass whose async_add_executor_job runs the sync callable inline."""

    async def async_add_executor_job(self, func, *args, **kwargs):
        return func(*args, **kwargs)


def _wrapper(api):
    wrapper = Edupage(_FakeHass(), sessionid="sess")
    wrapper.api = api
    return wrapper


async def test_login_raises_session_expired_when_not_logged_in():
    """reload_data() completes but the session is still not logged in."""
    api = MagicMock()
    api.is_logged_in = False

    with patch(
        "custom_components.homeassistantedupage.homeassistant_edupage.LoginSession",
        return_value=MagicMock(),
    ):
        with pytest.raises(EdupageSessionExpired):
            await _wrapper(api).login("user", "mshviezdoslavova1", "sess")


async def test_login_raises_session_expired_on_index_error():
    """reload_data() raises IndexError (expired session, homeassistantedupage#70)."""
    api = MagicMock()
    api.is_logged_in = True
    login_mock = MagicMock()
    login_mock.reload_data.side_effect = IndexError("list index out of range")

    with patch(
        "custom_components.homeassistantedupage.homeassistant_edupage.LoginSession",
        return_value=login_mock,
    ):
        with pytest.raises(EdupageSessionExpired):
            await _wrapper(api).login("user", "mshviezdoslavova1", "sess")


async def test_login_returns_true_on_valid_session():
    """A healthy reloaded session logs in successfully."""
    api = MagicMock()
    api.is_logged_in = True

    with patch(
        "custom_components.homeassistantedupage.homeassistant_edupage.LoginSession",
        return_value=MagicMock(),
    ):
        result = await _wrapper(api).login("user", "mshviezdoslavova1", "sess")
    assert result is True


async def test_get_meals_returns_none_on_index_error():
    """Empty meal data represented by IndexError is treated as unavailable."""
    api = MagicMock()
    api.get_meals.side_effect = IndexError("list index out of range")

    result = await _wrapper(api).get_meals("2026-09-04")

    assert result is None


async def test_get_meals_returns_none_on_attribute_error():
    """Unsupported meal data represented by AttributeError is treated as unavailable."""
    api = MagicMock()
    api.get_meals.side_effect = AttributeError("'list' object has no attribute 'keys'")

    result = await _wrapper(api).get_meals("2026-09-04")

    assert result is None


async def test_get_meals_raises_update_failed_on_unexpected_error():
    """Unexpected meal API failures remain visible to the coordinator."""
    api = MagicMock()
    api.get_meals.side_effect = RuntimeError("connection failed")

    with pytest.raises(UpdateFailed, match="connection failed"):
        await _wrapper(api).get_meals("2026-09-04")


async def test_get_timetable_uses_direct_result_when_permitted():
    """A working direct timetable request must not switch account context."""
    api = MagicMock()
    student = MagicMock()
    api.get_timetable.return_value = ["lesson"]

    result = await _wrapper(api).get_timetable(student, "2026-09-24")

    assert result == ["lesson"]
    api.get_timetable.assert_called_once_with(student, "2026-09-24")
    api.switch_to_child.assert_not_called()
    api.get_my_timetable.assert_not_called()
    api.switch_to_parent.assert_not_called()


async def test_get_timetable_retries_in_selected_child_context():
    """A permission rejection falls back to the parent child-switch flow."""
    api = MagicMock()
    student = MagicMock()
    api.get_timetable.side_effect = InsufficientPermissionsException(
        "Missing permissions"
    )
    api.get_my_timetable.return_value = ["child lesson"]

    result = await _wrapper(api).get_timetable(student, "2026-09-24")

    assert result == ["child lesson"]
    api.switch_to_child.assert_called_once_with(student)
    api.get_my_timetable.assert_called_once_with("2026-09-24")
    api.switch_to_parent.assert_called_once_with()


async def test_get_timetable_restores_parent_context_when_fallback_fails():
    """The parent context is restored even if the child request fails."""
    api = MagicMock()
    student = MagicMock()
    api.get_timetable.side_effect = InsufficientPermissionsException(
        "Missing permissions"
    )
    api.get_my_timetable.side_effect = RuntimeError("child request failed")

    with pytest.raises(UpdateFailed, match="child request failed"):
        await _wrapper(api).get_timetable(student, "2026-09-24")

    api.switch_to_child.assert_called_once_with(student)
    api.switch_to_parent.assert_called_once_with()


# ---------------------------------------------------------------------------
# Wrapper methods must surface API failures as UpdateFailed
# ---------------------------------------------------------------------------


async def test_get_grades_raises_update_failed_on_api_error():
    api = MagicMock()
    api.get_grades.side_effect = RuntimeError("non-numeric grade")
    with pytest.raises(UpdateFailed, match="get_grades"):
        await _wrapper(api).get_grades()


async def test_get_subjects_raises_update_failed_on_api_error():
    api = MagicMock()
    api.get_subjects.side_effect = IndexError("empty subjects")
    with pytest.raises(UpdateFailed, match="get_subjects"):
        await _wrapper(api).get_subjects()


async def test_get_notifications_raises_update_failed_on_api_error():
    api = MagicMock()
    api.get_notifications.side_effect = AttributeError("missing field")
    with pytest.raises(UpdateFailed, match="get_notifications"):
        await _wrapper(api).get_notifications()


async def test_numeric_and_term_grades_use_selected_child_and_restore_parent():
    from types import SimpleNamespace

    from edupage_api.grades import Term

    api = MagicMock()
    api.get_grades.return_value = ["grade"]
    api.get_grades_for_term.return_value = ["term-grade"]
    wrapper = _wrapper(api)
    student = SimpleNamespace(person_id=99)
    assert await wrapper.get_grades(student) == ["grade"]
    assert await wrapper.get_grades_for_term(2026, Term.FIRST, student) == [
        "term-grade"
    ]
    assert api.switch_to_child.call_count == 2
    assert api.switch_to_parent.call_count == 2


async def test_grade_error_logs_root_cause_without_session(caplog):
    import logging
    from types import SimpleNamespace

    api = MagicMock()
    api.get_grades.side_effect = ValueError("invalid numeric grade sess")
    wrapper = _wrapper(api)
    wrapper.sessionid = "sess"
    with caplog.at_level(logging.WARNING), pytest.raises(UpdateFailed):
        await wrapper.get_grades(SimpleNamespace(person_id=99))
    assert "ValueError: invalid numeric grade [redacted]" in caplog.text
    assert "library=" in caplog.text
    assert wrapper.grade_errors["get_grades"]["cause"].startswith("ValueError:")
    api.switch_to_parent.assert_called_once()


async def test_grade_reports_are_separate_for_current_and_terms():
    from edupage_api.grades import Term
    api = MagicMock()
    api.grade_read_report = {"received": 13, "parsed": 9, "skipped": 4}
    api.get_grades.return_value = []
    api.get_grades_for_term.return_value = []
    wrapper = _wrapper(api)
    await wrapper.get_grades()
    api.grade_read_report = {"received": 2, "parsed": 2, "skipped": 0}
    await wrapper.get_grades_for_term(2026, Term.FIRST)
    assert wrapper.grade_reports["grades"]["skipped"] == 4
    assert wrapper.grade_reports["term_P1"]["skipped"] == 0


@pytest.mark.parametrize("term", [False, True])
async def test_outer_exception_logging_cannot_restore_unredacted_grade_chain(caplog, term):
    import logging
    from types import SimpleNamespace
    from edupage_api.grades import Term

    api = MagicMock()
    api.username = "private-user@example.test"
    wrapper = _wrapper(api)
    wrapper.sessionid = "private-cookie-123"

    def fail(*args):
        try:
            raise ValueError("private-cookie-123 private-user@example.test")
        except ValueError as exc:
            raise RuntimeError("outer private-cookie-123") from exc

    api.get_grades.side_effect = fail
    api.get_grades_for_term.side_effect = fail
    with caplog.at_level(logging.WARNING):
        try:
            if term:
                await wrapper.get_grades_for_term(2026, Term.FIRST, SimpleNamespace(person_id=99))
            else:
                await wrapper.get_grades(SimpleNamespace(person_id=99))
        except UpdateFailed:
            # Reproduce the coordinator's actual logging, including exc_info.
            logging.getLogger(__name__).warning("Grade refresh failed", exc_info=True)
    assert "private-cookie-123" not in caplog.text
    assert "private-user@example.test" not in caplog.text
    assert "[redacted]" in caplog.text
    assert "RuntimeError" in caplog.text and "ValueError" in caplog.text
    assert "origin=" in caplog.text and "Traceback" in caplog.text
    assert "direct cause" not in caplog.text


async def test_parent_restore_failure_keeps_read_and_quarantines_session(caplog):
    from types import SimpleNamespace
    from edupage_api.exceptions import UnknownServerError

    api = MagicMock()
    api.get_grades.return_value = ["verified grade"]
    api.grade_read_report = {"received": 1, "parsed": 1, "skipped": 0}
    api.switch_to_parent.side_effect = UnknownServerError()
    wrapper = _wrapper(api)
    assert await wrapper.get_grades(SimpleNamespace(person_id=99)) == ["verified grade"]
    assert wrapper.grade_reports["grades"]["parsed"] == 1
    assert wrapper.context_invalid
    with pytest.raises(UpdateFailed):
        await wrapper.get_subjects()
    api.get_subjects.assert_not_called()
    with pytest.raises(UpdateFailed):
        await wrapper.consultation_action(SimpleNamespace(person_id=100), "book_consultation_slot", dry_run=False)
    api.book_consultation_slot.assert_not_called()
    assert "UnknownServerError" in caplog.text


async def test_cleanup_failure_does_not_replace_primary_grade_error():
    from types import SimpleNamespace
    from edupage_api.exceptions import UnknownServerError

    api = MagicMock()
    api.get_grades.side_effect = ValueError("primary parse failure")
    api.switch_to_parent.side_effect = UnknownServerError()
    wrapper = _wrapper(api)
    with pytest.raises(UpdateFailed):
        await wrapper.get_grades(SimpleNamespace(person_id=99))
    assert "primary parse failure" in wrapper.grade_errors["get_grades"]["cause"]
    assert wrapper.context_invalid


async def test_context_recovery_uses_reloaded_identity_before_other_child():
    from types import SimpleNamespace
    from edupage_api.exceptions import UnknownServerError

    api = MagicMock()
    api.get_grades.return_value = ["child 1"]
    api.switch_to_parent.side_effect = [UnknownServerError(), None, None]
    wrapper = _wrapper(api)
    assert await wrapper.get_grades(SimpleNamespace(person_id=1)) == ["child 1"]
    with patch("custom_components.homeassistantedupage.homeassistant_edupage.LoginSession") as login:
        await wrapper.login("canonical-parent", "school", "new-session")
        login.return_value.reload_data.assert_called_once_with("school", "new-session", "canonical-parent")
    assert not wrapper.context_invalid
    api.get_grades.return_value = ["child 2"]
    assert await wrapper.get_grades(SimpleNamespace(person_id=2)) == ["child 2"]
    assert [call.args[0].person_id for call in api.switch_to_child.call_args_list] == [1, 2]


async def test_context_failure_after_successful_login_is_not_auth_failure():
    from homeassistant.exceptions import ConfigEntryAuthFailed
    from edupage_api.exceptions import UnknownServerError

    api = MagicMock()
    api.switch_to_parent.side_effect = UnknownServerError()
    wrapper = _wrapper(api)
    wrapper.context_invalid = True
    with patch("custom_components.homeassistantedupage.homeassistant_edupage.LoginSession"):
        with pytest.raises(UpdateFailed, match="session reload succeeded") as caught:
            await wrapper.login("parent", "school", "session")
    assert not isinstance(caught.value, ConfigEntryAuthFailed)
    assert not wrapper.auth_failed
    assert wrapper.context_invalid
    api.get_grades.assert_not_called()


async def test_mid_read_expiry_is_auth_failure_and_stops_next_network_call():
    from edupage_api.exceptions import ExpiredSessionException
    from homeassistant.exceptions import ConfigEntryAuthFailed

    api = MagicMock()
    api.get_subjects.side_effect = ExpiredSessionException()
    wrapper = _wrapper(api)
    wrapper._verify_account_session = MagicMock(return_value=False)
    with pytest.raises(ConfigEntryAuthFailed):
        await wrapper.get_subjects()
    with pytest.raises(ConfigEntryAuthFailed):
        await wrapper.get_students()
    api.get_students.assert_not_called()


@pytest.mark.parametrize("body,status,url", [
    ('userhome({"userid":"Rodic1"});<input type="password">', 200, "https://school.edupage.org/user/"),
    ('maintenance', 503, "https://school.edupage.org/user/"),
    ('malformed bootstrap', 200, "https://school.edupage.org/user/"),
])
async def test_module_auth_signal_requires_account_confirmation(body, status, url):
    from edupage_api.exceptions import ExpiredSessionException
    from homeassistant.exceptions import ConfigEntryAuthFailed

    api = MagicMock()
    api.subdomain = "school"
    api.get_subjects.side_effect = ExpiredSessionException("PRIVATE MODULE PAYLOAD")
    api.session.get.return_value = MagicMock(text=body, status_code=status, url=url)
    wrapper = _wrapper(api)
    with pytest.raises(UpdateFailed, match="reauthentication not requested") as caught:
        await wrapper.get_subjects()
    assert not isinstance(caught.value, ConfigEntryAuthFailed)
    assert not wrapper.auth_failed
    assert "PRIVATE" not in str(caught.value)
    api.session.get.assert_called_once_with("https://school.edupage.org/user/")


async def test_confirmed_account_login_redirect_is_auth_failure():
    from edupage_api.exceptions import ExpiredSessionException
    from homeassistant.exceptions import ConfigEntryAuthFailed

    api = MagicMock()
    api.subdomain = "school"
    api.get_subjects.side_effect = ExpiredSessionException()
    api.session.get.return_value = MagicMock(status_code=200, url="https://school.edupage.org/login/")
    api.get_subjects.__name__ = "get_subjects"
    wrapper = _wrapper(api)
    with pytest.raises(ConfigEntryAuthFailed, match="get_subjects"):
        await wrapper.get_subjects()
    assert wrapper.auth_failed


async def test_null_timetable_rpc_gets_one_verified_child_read():
    import json
    from datetime import date
    from edupage_api import Edupage as Client
    from edupage_api.people import EduStudent
    from edupage_api.timetables import Timetables

    api = Client()
    api.is_logged_in = True
    api.data = {"userid": "Rodic1"}
    api.subdomain = "school"
    api.username = "parent"
    api.session = MagicMock()
    api.session.get.return_value = MagicMock(text="OK", url="https://school.edupage.org/user/")
    student = EduStudent.__new__(EduStudent)
    student.person_id = -1
    contexts = []

    def timetable_response(*args, **kwargs):
        contexts.append(api._selected_child_id)
        result = None if len(contexts) == 1 else {"ttitems": []}
        return MagicMock(content=json.dumps({"r": result}).encode())

    api.session.post.side_effect = timetable_response
    wrapper = _wrapper(api)
    with patch.object(Timetables, "get_school_year", return_value=2026), patch(
        "edupage_api.timetables.People.get_student", return_value=student,
    ):
        result = await wrapper.get_timetable(student, date(2026, 11, 10))
    assert list(result) == []
    assert contexts == [None, -1]
    assert not wrapper.auth_failed
    assert api._selected_child_id is None
    assert all(call.kwargs["json"]["__args"][1]["id"] == "-1" for call in api.session.post.call_args_list)


async def test_failed_child_timetable_read_is_not_retried_or_reauthenticated():
    from edupage_api.exceptions import MissingDataException
    from homeassistant.exceptions import ConfigEntryAuthFailed

    api = MagicMock()
    api.get_timetable.side_effect = MissingDataException("r is null")
    api.get_my_timetable.side_effect = MissingDataException("r still null")
    wrapper = _wrapper(api)
    with pytest.raises(UpdateFailed) as caught:
        await wrapper.get_timetable(MagicMock(), "2026-11-10")
    assert not isinstance(caught.value, ConfigEntryAuthFailed)
    assert not wrapper.auth_failed
    api.get_timetable.assert_called_once()
    api.get_my_timetable.assert_called_once()
    api.switch_to_parent.assert_called_once()


async def test_false_grade_auth_signal_does_not_log_suppressed_payload(caplog):
    from edupage_api.exceptions import ExpiredSessionException

    api = MagicMock()
    api.subdomain = "school"
    api.get_grades.side_effect = ExpiredSessionException("PRIVATE MODULE PAYLOAD")
    api.session.get.return_value = MagicMock(
        status_code=200, url="https://school.edupage.org/user/",
        text='userhome({"userid":"Rodic1"});',
    )
    wrapper = _wrapper(api)
    with pytest.raises(UpdateFailed):
        await wrapper.get_grades()
    assert "PRIVATE MODULE PAYLOAD" not in caplog.text
    assert not wrapper.auth_failed
