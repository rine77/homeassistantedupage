"""Privacy-safe diagnostics for the EduPage integration."""

from __future__ import annotations

import re
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
from .grade_helpers import grade_reference_ids

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
    grade_events = [
        item for item in notifications if _event_type(item) == "znamka"
    ]
    timeline_grade_ids = {
        str(event_id)
        for item in grade_events
        if (event_id := getattr(item, "event_id", None)) is not None
    }
    embedded_grade_ids = (
        set().union(*(grade_reference_ids(item) for item in grade_events))
        if grade_events
        else set()
    )
    direct_matches = grade_event_ids & timeline_grade_ids
    embedded_matches = grade_event_ids & embedded_grade_ids
    matched_events = sum(
        bool(
            grade_event_ids
            & (
                grade_reference_ids(item)
                | {
                    str(item.event_id)
                }
            )
        )
        for item in grade_events
    )
    linkage = _grade_linkage_diagnostics(grades, notifications)
    return {
        "count": _safe_len(grades),
        "field_coverage": {
            label: sum(getattr(grade, attribute, None) is not None for grade in grades)
            for label, attribute in _GRADE_FIELDS.items()
        },
        "timeline_grade_events": _safe_len(timeline_grade_ids),
        "matched_to_timeline_event": matched_events,
        "unmatched_timeline_events": _safe_len(grade_events) - matched_events,
        "direct_event_id_matches": _safe_len(direct_matches),
        "embedded_event_id_matches": _safe_len(embedded_matches),
        "linkage_diagnostics": linkage,
    }


def _normalized_scalar(value: Any) -> str | None:
    """Normalize an identifier-like scalar without exporting the value."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (str, int, float)):
        return str(value).strip()
    return None


def _date_value(value: Any) -> date | None:
    """Return a date from a date, datetime, or ISO-like value."""
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


def _grade_linkage_diagnostics(
    grades: list[Any], notifications: list[Any]
) -> dict[str, Any]:
    """Describe safe linkage signals without exposing IDs or grade content."""
    grade_events = [item for item in notifications if _event_type(item) == "znamka"]
    grade_ids = {
        value
        for grade in grades
        if (value := _normalized_scalar(getattr(grade, "event_id", None)))
    }
    subject_ids = {
        value
        for grade in grades
        if (value := _normalized_scalar(getattr(grade, "subject_id", None)))
    }
    schema: dict[str, Counter[str]] = {}
    grade_reference_paths = Counter()
    subject_reference_paths = Counter()
    traversal = {"nodes": 0, "dynamic_keys": 0, "truncated": False}

    for event in grade_events:
        additional_data = getattr(event, "additional_data", None) or {}
        _inspect_structure(
            additional_data,
            "$",
            grade_ids,
            subject_ids,
            schema,
            grade_reference_paths,
            subject_reference_paths,
            traversal,
        )

    same_day_pairs = 0
    same_subject_pairs = 0
    same_title_pairs = 0
    for grade in grades:
        grade_day = _date_value(getattr(grade, "date", None))
        grade_subject = _normalized_scalar(getattr(grade, "subject_id", None))
        grade_title = str(getattr(grade, "title", None) or "").strip().casefold()
        for event in grade_events:
            event_day = _date_value(getattr(event, "timestamp", None))
            additional_data = getattr(event, "additional_data", None) or {}
            event_subject = (
                _normalized_scalar(additional_data.get("predmetid"))
                if isinstance(additional_data, dict)
                else None
            )
            event_text = str(getattr(event, "text", None) or "").strip().casefold()
            same_day_pairs += grade_day is not None and grade_day == event_day
            same_subject_pairs += (
                grade_subject is not None and grade_subject == event_subject
            )
            same_title_pairs += bool(grade_title and grade_title == event_text)

    return {
        "additional_data_schema": {
            key: dict(sorted(types.items()))
            for key, types in sorted(schema.items())
        },
        "grade_id_reference_paths": dict(sorted(grade_reference_paths.items())),
        "subject_id_reference_paths": dict(
            sorted(subject_reference_paths.items())
        ),
        "dynamic_key_count": traversal["dynamic_keys"],
        "traversal_truncated": traversal["truncated"],
        "same_day_pairs": same_day_pairs,
        "same_subject_pairs": same_subject_pairs,
        "same_title_pairs": same_title_pairs,
    }


def _safe_schema_key(value: Any) -> tuple[str, bool]:
    """Return a structural key or an anonymous marker for dynamic keys."""
    key = str(value)
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", key):
        return key, False
    return "<dynamic_key>", True


def _inspect_structure(
    value: Any,
    path: str,
    grade_ids: set[str],
    subject_ids: set[str],
    schema: dict[str, Counter[str]],
    grade_reference_paths: Counter[str],
    subject_reference_paths: Counter[str],
    traversal: dict[str, Any],
    depth: int = 0,
) -> None:
    """Inspect bounded JSON structure while retaining no scalar values."""
    if traversal["nodes"] >= 500 or depth > 6:
        traversal["truncated"] = True
        return
    traversal["nodes"] += 1
    schema.setdefault(path, Counter())[type(value).__name__] += 1

    normalized = _normalized_scalar(value)
    if normalized in grade_ids:
        grade_reference_paths[path] += 1
    if normalized in subject_ids:
        subject_reference_paths[path] += 1

    if isinstance(value, dict):
        for raw_key, item in value.items():
            key, dynamic = _safe_schema_key(raw_key)
            traversal["dynamic_keys"] += dynamic
            _inspect_structure(
                item,
                f"{path}.{key}",
                grade_ids,
                subject_ids,
                schema,
                grade_reference_paths,
                subject_reference_paths,
                traversal,
                depth + 1,
            )
    elif isinstance(value, (list, tuple)):
        for item in value:
            _inspect_structure(
                item,
                f"{path}[]",
                grade_ids,
                subject_ids,
                schema,
                grade_reference_paths,
                subject_reference_paths,
                traversal,
                depth + 1,
            )


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
    error_types = _exception_type_chain(last_exception)
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
        "error_types": error_types,
    }


def _exception_type_chain(exception: BaseException | None) -> list[str]:
    """Return bounded exception class names without messages or arguments."""
    result = []
    seen = set()
    current = exception
    while current is not None and id(current) not in seen and len(result) < 5:
        seen.add(id(current))
        result.append(type(current).__name__)
        current = current.__cause__ or current.__context__
    return result


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
