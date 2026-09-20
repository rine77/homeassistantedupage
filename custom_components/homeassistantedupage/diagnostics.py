"""Privacy-safe diagnostics for the EduPage integration."""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration
from homeassistant.util import dt as dt_util

from .assignment_helpers import event_matches_student
from .const import (
    CONF_PHPSESSID,
    CONF_STUDENT_ID,
    CONF_STUDENT_NAME,
    CONF_SUBJECT_IDS,
    DOMAIN,
)

DIAGNOSTICS_SCHEMA_VERSION = 1

_HOMEWORK_TYPE = "homework"
_EXAM_TYPES = {
    "bexam",
    "oexam",
    "rexam",
    "pexam",
    "sexam",
    "testing",
    "testpridelenie",
}
_GRADE_FIELDS = {
    "numeric_grade": "grade_n",
    "percentage": "percent",
    "max_points": "max_points",
    "class_average": "class_grade_avg",
    "title": "title",
    "comment": "comment",
    "teacher": "teacher",
    "event_id": "event_id",
}


def _safe_len(value: Any) -> int:
    """Return the length of a value without assuming it is sized."""
    if value is None:
        return 0
    try:
        return len(value)
    except TypeError:
        return 0


def _event_type(event: Any) -> str:
    """Return a stable string for an EduPage timeline event type."""
    event_type = getattr(event, "event_type", None)
    return str(getattr(event_type, "value", event_type) or "")


def _event_date(event: Any) -> date | None:
    """Return the date stored in an assignment notification, if valid."""
    additional_data = getattr(event, "additional_data", None) or {}
    value = additional_data.get("date")
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except (TypeError, ValueError):
        return None


def _nested_item_count(value: Any) -> int:
    """Count items held in a mapping of dates or terms to collections."""
    if not isinstance(value, dict):
        return 0
    return sum(_safe_len(items) for items in value.values())


def _package_version(package: str) -> str | None:
    """Return an installed package version without failing diagnostics."""
    try:
        return version(package)
    except PackageNotFoundError:
        return None


def _configuration_summary(entry: ConfigEntry) -> dict[str, Any]:
    """Build an allowlisted summary without copying config-entry values."""
    selected_subjects = entry.options.get(CONF_SUBJECT_IDS)
    return {
        "authentication": {
            "username_configured": bool(entry.data.get(CONF_USERNAME)),
            "session_configured": bool(entry.data.get(CONF_PHPSESSID)),
            "password_stored": "password" in entry.data,
        },
        "student": {
            "id_configured": bool(entry.data.get(CONF_STUDENT_ID)),
            "name_configured": bool(entry.data.get(CONF_STUDENT_NAME)),
        },
        "subject_selection": {
            "configured": selected_subjects is not None,
            "selected_count": _safe_len(selected_subjects),
        },
    }


def _section_summary(data: dict[str, Any]) -> dict[str, Any]:
    """Summarize the freshness and size of every fetched data section."""
    data_ok = data.get("data_ok") or {}
    section_values = {
        "grades": data.get("grades"),
        "subjects": data.get("subjects"),
        "notifications": data.get("notifications"),
        "timetable": data.get("timetable"),
        "canteen_menu": data.get("canteen_menu"),
        "timetable_changes": data.get("timetable_changes"),
        "missing_teachers": data.get("missing_teachers"),
        "grades_per_term": data.get("grades_per_term"),
    }
    result = {
        key: {
            "success": bool(data_ok.get(key)),
            "items": (
                _nested_item_count(value)
                if key in {"timetable", "canteen_menu", "grades_per_term"}
                else _safe_len(value)
            ),
        }
        for key, value in section_values.items()
    }
    result["school_year"] = {
        "success": bool(data_ok.get("school_year")),
        "available": data.get("school_year") is not None,
    }
    result["next_ringing"] = {
        "success": bool(data_ok.get("next_ringing")),
        "available": data.get("next_ringing") is not None,
    }
    return result


def _assignment_summary(data: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Summarize student filtering and assignment processing."""
    notifications = data.get("notifications") or []
    student = data.get("student") or {}
    student_id = student.get("id")
    student_name = student.get("name")
    class_names = student.get("class_names") or []

    matching = [
        item
        for item in notifications
        if event_matches_student(item, student_id, student_name, class_names)
    ]
    missing_recipient = sum(
        getattr(item, "recipient", None) in (None, "", "*")
        for item in notifications
    )

    homework = [item for item in matching if _event_type(item) == _HOMEWORK_TYPE]
    exams = [item for item in matching if _event_type(item) in _EXAM_TYPES]
    today = dt_util.now().date()
    dated_homework = [(item, _event_date(item)) for item in homework]
    dated_exams = [(item, _event_date(item)) for item in exams]

    filter_summary = {
        "student_available": bool(student),
        "class_id_available": student.get("class_id") is not None,
        "class_name_count": _safe_len(class_names),
        "notifications_total": _safe_len(notifications),
        "notifications_matching_student": _safe_len(matching),
        "notifications_without_recipient_information": missing_recipient,
    }
    assignment_summary = {
        "homework_total": _safe_len(homework),
        "homework_open": sum(
            not bool(getattr(item, "is_done", False)) for item in homework
        ),
        "homework_overdue": sum(
            not bool(getattr(item, "is_done", False))
            and due is not None
            and due < today
            for item, due in dated_homework
        ),
        "homework_without_date": sum(due is None for _, due in dated_homework),
        "exams_total": _safe_len(exams),
        "exams_upcoming": sum(
            exam_date is not None and exam_date >= today
            for _, exam_date in dated_exams
        ),
    }
    return filter_summary, assignment_summary


def _grade_summary(data: dict[str, Any]) -> dict[str, Any]:
    """Describe grade field coverage without exposing grade values."""
    grades = data.get("grades") or []
    notifications = data.get("notifications") or []
    grade_event_ids = {
        str(event_id)
        for grade in grades
        if (event_id := getattr(grade, "event_id", None)) is not None
    }
    timeline_grade_ids = {
        str(event_id)
        for item in notifications
        if _event_type(item) == "znamka"
        and (event_id := getattr(item, "event_id", None)) is not None
    }
    return {
        "count": _safe_len(grades),
        "field_coverage": {
            label: sum(getattr(grade, attribute, None) is not None for grade in grades)
            for label, attribute in _GRADE_FIELDS.items()
        },
        "timeline_grade_events": _safe_len(timeline_grade_ids),
        "matched_to_timeline_event": _safe_len(grade_event_ids & timeline_grade_ids),
        "unmatched_timeline_events": _safe_len(timeline_grade_ids - grade_event_ids),
    }


def _capability_summary(data: dict[str, Any] | None) -> dict[str, Any]:
    """Build a JSON-safe summary from the coordinator snapshot."""
    data = data or {}
    notifications = data.get("notifications") or []
    student_filter, assignments = _assignment_summary(data)
    return {
        "sections": _section_summary(data),
        "student_filter": student_filter,
        "grades": _grade_summary(data),
        "assignments": assignments,
        "notifications": {
            "count": _safe_len(notifications),
            "types": dict(sorted(Counter(_event_type(item) for item in notifications).items())),
        },
        "timetable": {
            "days_with_lessons": _safe_len(data.get("timetable")),
            "lesson_count": _nested_item_count(data.get("timetable")),
            "days_with_cancelled_lessons": _safe_len(data.get("cancelled_lessons")),
            "cancelled_lesson_count": _nested_item_count(data.get("cancelled_lessons")),
        },
        "canteen": {
            "days_with_menu": _safe_len(data.get("canteen_menu")),
            "meal_type_count": _nested_item_count(data.get("canteen_menu")),
        },
        "last_updated": data.get("last_updated"),
    }


def _runtime_summary(coordinator: Any) -> dict[str, Any]:
    """Describe coordinator health without exposing exception messages."""
    data_available = bool(getattr(coordinator, "data", None))
    update_success = bool(getattr(coordinator, "last_update_success", False))
    if update_success and data_available:
        state = "healthy"
    elif update_success:
        state = "empty_success"
    elif data_available:
        state = "stale_data"
    else:
        state = "no_data"

    update_interval = getattr(coordinator, "update_interval", None)
    last_exception = getattr(coordinator, "last_exception", None)
    return {
        "loaded": True,
        "state": state,
        "last_update_success": update_success,
        "update_interval_seconds": (
            update_interval.total_seconds()
            if update_interval is not None
            else None
        ),
        "coordinator_data_available": data_available,
        "last_error_type": (
            type(last_exception).__name__ if last_exception is not None else None
        ),
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return privacy-safe diagnostics for an EduPage config entry."""
    integration = await async_get_integration(hass, DOMAIN)
    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    result = {
        "diagnostics_schema": DIAGNOSTICS_SCHEMA_VERSION,
        "integration": {
            "version": integration.version,
            "edupage_api_version": _package_version("edupage-api"),
        },
        "configuration": _configuration_summary(entry),
        "runtime": {
            "loaded": coordinator is not None,
        },
    }
    if coordinator is None:
        return result

    result["runtime"] = _runtime_summary(coordinator)
    result["capabilities"] = _capability_summary(
        getattr(coordinator, "data", None)
    )
    return result
