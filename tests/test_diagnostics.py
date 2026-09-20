"""Tests for privacy-safe EduPage diagnostics."""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import CONF_USERNAME

from custom_components.homeassistantedupage.const import (
    CONF_PHPSESSID,
    CONF_STUDENT_ID,
    CONF_STUDENT_NAME,
    CONF_SUBDOMAIN,
    CONF_SUBJECT_IDS,
    DOMAIN,
)
from custom_components.homeassistantedupage.diagnostics import (
    _capability_summary,
    async_get_config_entry_diagnostics,
)


def _event(event_type, event_id, **kwargs):
    return SimpleNamespace(
        event_type=SimpleNamespace(value=event_type),
        event_id=event_id,
        **kwargs,
    )


def test_capability_summary_explains_filtering_assignments_and_grades():
    """Diagnostics expose processing counts, never the underlying content."""
    today = datetime.now(UTC).date()
    data = {
        "student": {
            "id": "student-123",
            "name": "Private Student",
            "class_id": "class-1",
            "class_names": ["7A"],
        },
        "grades": [
            SimpleNamespace(
                event_id=10,
                grade_n="1",
                percent=95,
                max_points=20,
                class_grade_avg=2.3,
                title="Secret test",
                comment=None,
                teacher=SimpleNamespace(name="Private Teacher"),
            ),
            SimpleNamespace(event_id=99, grade_n="2"),
        ],
        "subjects": [object(), object()],
        "notifications": [
            _event("znamka", 10, recipient="Private Student"),
            _event(
                "homework",
                11,
                recipient="Private Student",
                is_done=False,
                text="Secret homework",
                additional_data={"date": (today - timedelta(days=1)).isoformat()},
            ),
            _event(
                "homework",
                12,
                recipient="Other Student",
                is_done=False,
                additional_data={},
            ),
            _event(
                "testing",
                13,
                recipient="7A",
                additional_data={"date": (today + timedelta(days=1)).isoformat()},
            ),
            _event("sprava", 14, recipient=None),
        ],
        "timetable": {today: [object(), object()]},
        "cancelled_lessons": {today: [object()]},
        "canteen_menu": {today: [object()]},
        "timetable_changes": [object()],
        "missing_teachers": [],
        "grades_per_term": {"first": [object()], "second": []},
        "school_year": 2026,
        "next_ringing": object(),
        "data_ok": {
            "grades": True,
            "subjects": True,
            "notifications": True,
            "timetable": True,
            "canteen_menu": True,
            "timetable_changes": True,
            "missing_teachers": True,
            "grades_per_term": False,
            "school_year": True,
            "next_ringing": True,
        },
        "last_updated": "2026-09-20T12:00:00",
    }

    summary = _capability_summary(data)

    assert summary["student_filter"]["notifications_total"] == 5
    assert summary["student_filter"]["notifications_matching_student"] == 4
    assert summary["student_filter"]["notifications_without_recipient_information"] == 1
    assert summary["assignments"] == {
        "homework_total": 1,
        "homework_open": 1,
        "homework_overdue": 1,
        "homework_without_date": 0,
        "exams_total": 1,
        "exams_upcoming": 1,
    }
    assert summary["grades"]["field_coverage"]["numeric_grade"] == 2
    assert summary["grades"]["field_coverage"]["comment"] == 0
    assert summary["grades"]["matched_to_timeline_event"] == 1
    assert summary["grades"]["unmatched_timeline_events"] == 0
    assert summary["sections"]["grades_per_term"] == {
        "success": False,
        "items": 1,
    }
    assert summary["timetable"]["lesson_count"] == 2

    serialized = json.dumps(summary)
    for secret in (
        "student-123",
        "Private Student",
        "class-1",
        "7A",
        "Secret test",
        "Secret homework",
        "Private Teacher",
        "95",
    ):
        assert secret not in serialized


@pytest.mark.asyncio
async def test_diagnostics_use_allowlisted_configuration_and_runtime_summary():
    """Config secrets and unknown future fields cannot leak into diagnostics."""
    coordinator = SimpleNamespace(
        data={"student": {}, "notifications": [], "data_ok": {}},
        last_update_success=True,
        update_interval=timedelta(minutes=30),
    )
    hass = SimpleNamespace(data={DOMAIN: {"entry-1": coordinator}})
    entry = SimpleNamespace(
        entry_id="entry-1",
        data={
            CONF_USERNAME: "parent@example.test",
            CONF_PHPSESSID: "secret-session",
            CONF_SUBDOMAIN: "private-school",
            CONF_STUDENT_ID: "student-123",
            CONF_STUDENT_NAME: "Private Student",
            "future_secret": "must-never-leak",
        },
        options={CONF_SUBJECT_IDS: ["math", "english"]},
    )

    with patch(
        "custom_components.homeassistantedupage.diagnostics.async_get_integration",
        new=AsyncMock(return_value=SimpleNamespace(version="0.9.0")),
    ):
        diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    serialized = json.dumps(diagnostics)
    for secret in entry.data.values():
        assert secret not in serialized
    assert diagnostics["diagnostics_schema"] == 1
    assert diagnostics["integration"]["version"] == "0.9.0"
    assert diagnostics["configuration"]["subject_selection"]["selected_count"] == 2
    assert diagnostics["runtime"]["update_interval_seconds"] == 1800
    assert diagnostics["runtime"]["coordinator_data_available"] is True


@pytest.mark.asyncio
async def test_diagnostics_handle_unloaded_entry():
    """An unloaded config entry still provides a safe configuration summary."""
    hass = SimpleNamespace(data={DOMAIN: {}})
    entry = SimpleNamespace(
        entry_id="entry-1",
        data={CONF_USERNAME: "parent@example.test"},
        options={},
    )

    with patch(
        "custom_components.homeassistantedupage.diagnostics.async_get_integration",
        new=AsyncMock(return_value=SimpleNamespace(version="0.9.0")),
    ):
        diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics["runtime"] == {"loaded": False}
    assert "capabilities" not in diagnostics
    assert "parent@example.test" not in json.dumps(diagnostics)
