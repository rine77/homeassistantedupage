"""Retain useful failure stacks and correlation without exposing private data."""

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from edupage_api.exceptions import UnknownServerError
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.homeassistantedupage.diagnostic_logging import log_exception
from custom_components.homeassistantedupage.homeassistant_edupage import Edupage


def test_original_frames_and_exception_types_without_messages_or_source(caplog):
    def fail():
        try:
            raise ValueError("PRIVATE_USER PRIVATE_COOKIE")
        except ValueError as exc:
            raise UnknownServerError("PRIVATE_RID PRIVATE_PAYLOAD") from exc

    try:
        fail()
    except UnknownServerError as exc:
        log_exception(logging.getLogger(__name__), "parent_recovery", exc, entry_id="entry-A", student_id=-1)
    assert "UnknownServerError" in caplog.text
    assert "ValueError" in caplog.text
    assert "in fail" in caplog.text
    assert "test_diagnostic_logging.py" in caplog.text
    assert "entry_id=entry-A student_id=-1" in caplog.text
    assert "PRIVATE" not in caplog.text


async def test_session_failure_logs_start_response_and_original_parent_frame(caplog):
    class FakeHass:
        async def async_add_executor_job(self, function, *args):
            return function(*args)

    wrapper = Edupage(FakeHass())
    wrapper.entry_id, wrapper.student_id = "entry-A", -1
    wrapper.context_invalid = True
    api = wrapper.api
    api.subdomain = "school"
    api._diagnostic_entry_id = "entry-A"
    api._diagnostic_student_id = -1
    api.session = MagicMock()
    page = 'userhome({"userid":"Rodic1"});'
    api.session.get.side_effect = [
        SimpleNamespace(content=page.encode(), text=page, status_code=200,
                        url="https://school.edupage.org/user/", headers={}, history=[]),
        SimpleNamespace(status_code=200, text="PRIVATE_BODY", content=b"PRIVATE_BODY",
                        url="https://school.edupage.org/login/?EdupageLoginFailed=1&rid=PRIVATE_RID",
                        headers={"Set-Cookie": "PRIVATE_COOKIE"}, history=[], raise_for_status=lambda: None),
    ]
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(UpdateFailed):
            await wrapper.login("PRIVATE_USER", "school", "PRIVATE_COOKIE")
    assert "Session reload start" in caplog.text
    assert "Session reload succeeded" in caplog.text
    assert "parent_switch request" in caplog.text
    assert "parent_switch response" in caplog.text
    assert "parent_context_recovery" in caplog.text
    assert "parent.py" in caplog.text and "switch_to_parent" in caplog.text
    assert "UnknownServerError: Parent switch redirected to login" in caplog.text
    assert "entry_id=entry-A student_id=-1" in caplog.text
    assert "PRIVATE" not in caplog.text
    assert not wrapper.auth_failed


async def test_debug_logging_does_not_dump_notifications_or_lessons(caplog):
    class FakeHass:
        async def async_add_executor_job(self, function, *args):
            return function(*args)

    wrapper = Edupage(FakeHass())
    wrapper.api = MagicMock()
    wrapper.api.get_notifications.return_value = ["PRIVATE_NOTICE"]
    wrapper.api.get_timetable.return_value = ["PRIVATE_LESSON"]
    with caplog.at_level(logging.DEBUG):
        assert await wrapper.get_notifications() == ["PRIVATE_NOTICE"]
        assert await wrapper.get_timetable(SimpleNamespace(person_id=1), "2026-11-10") == ["PRIVATE_LESSON"]
    assert "PRIVATE" not in caplog.text
