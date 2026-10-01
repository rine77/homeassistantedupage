"""Text assessment display, recovery and privacy tests."""
from datetime import datetime
import json
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

from homeassistant.core import State
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from custom_components.homeassistantedupage.sensor import EduPageTextAssessmentSensor
from custom_components.homeassistantedupage.diagnostics import _capability_summary
from custom_components.homeassistantedupage.homeassistant_edupage import Edupage


def grade(id=1, text="Good participation", date=datetime(2026, 9, 30)):
    return SimpleNamespace(grade_id=id, comment=text, grade_type="praise",
                           date=date, subject_id=7, subject_name="Behavior")


def sensor(hass, grades):
    coordinator = DataUpdateCoordinator(hass, logging.getLogger(__name__), name="test", config_entry=None)
    coordinator.data = {"text_grades": grades, "data_ok": {"text_grades": True}}
    coordinator.last_update_success = True
    return EduPageTextAssessmentSensor(coordinator, 123, "Student")


def test_latest_and_missing_date(hass):
    entity = sensor(hass, [grade(1, date=None), grade(2, date=datetime(2026, 9, 29)), grade(3)])
    assert entity.state == 3
    attrs = entity.extra_state_attributes
    assert [item["id"] for item in attrs["assessments"]] == [3, 2, 1]
    assert attrs["latest"]["text"] == "Good participation"
    assert attrs["latest"]["subject"] == "Behavior"
    assert attrs["data_stale"] is False


def test_outage_and_empty_recovery(hass):
    entity = sensor(hass, [grade()])
    assert entity.state == 1
    previous = entity.extra_state_attributes["latest"]
    entity.coordinator.data = {"text_grades": [], "data_ok": {"text_grades": False}}
    assert entity.state == 1
    assert entity.extra_state_attributes["latest"] == previous
    assert entity.extra_state_attributes["data_stale"] is True
    entity.coordinator.data["data_ok"]["text_grades"] = True
    assert entity.state == 0
    assert entity.extra_state_attributes["latest"] is None


def test_first_failure_unavailable_and_restore(hass):
    entity = sensor(hass, [])
    entity.coordinator.data["data_ok"]["text_grades"] = False
    assert not entity.available
    entity._apply_restored(State("sensor.example", "2", {"latest": {"text": "Restored"}}))
    assert entity.available
    assert entity.state == 2
    assert entity.extra_state_attributes["latest"]["text"] == "Restored"
    assert entity.data_stale


def test_bounded_attributes(hass):
    entity = sensor(hass, [grade(i, text="č" * 1000) for i in range(100)])
    assert entity.state == 100
    attrs = entity.extra_state_attributes
    assert 0 < attrs["assessments_exposed"] < 50
    assert attrs["assessments_truncated"]
    assert len(json.dumps(attrs, ensure_ascii=False).encode()) < 14 * 1024


def test_oversized_first_item(hass):
    entity = sensor(hass, [grade(text="x" * 20000)])
    assert entity.state == 1
    assert entity.extra_state_attributes["assessments"] == []
    assert entity.extra_state_attributes["assessments_truncated"]


def test_diagnostics_exclude_contents():
    summary = _capability_summary({"text_grades": [grade()], "data_ok": {"text_grades": True}})
    assert summary["sections"]["text_grades"] == {"success": True, "items": 1}
    assert "Good participation" not in json.dumps(summary)
    assert "Behavior" not in json.dumps(summary)


async def test_wrapper_uses_executor():
    class Hass:
        async def async_add_executor_job(self, func, *args):
            return func(*args)
    wrapper = Edupage(Hass())
    wrapper.api = MagicMock()
    wrapper.api.get_text_grades.return_value = [grade()]
    assert len(await wrapper.get_text_grades(123)) == 1
    wrapper.api.get_text_grades.assert_called_once_with()

    wrapper.api.switch_to_child.assert_called_once_with(123)
    wrapper.api.switch_to_parent.assert_called_once_with()


async def test_child_context_restored_after_failure():
    import pytest
    from homeassistant.helpers.update_coordinator import UpdateFailed
    class Hass:
        async def async_add_executor_job(self, func, *args):
            return func(*args)
    wrapper = Edupage(Hass())
    wrapper.api = MagicMock()
    wrapper.api.get_text_grades.side_effect = ValueError("parse error")
    with pytest.raises(UpdateFailed):
        await wrapper.get_text_grades(123)
    wrapper.api.switch_to_parent.assert_called_once_with()


async def test_student_account_needs_no_parent_restore():
    from edupage_api.exceptions import NotParentException
    class Hass:
        async def async_add_executor_job(self, func, *args):
            return func(*args)
    wrapper = Edupage(Hass())
    wrapper.api = MagicMock()
    wrapper.api.switch_to_child.side_effect = NotParentException()
    wrapper.api.get_text_grades.return_value = []
    assert await wrapper.get_text_grades(123) == []
    wrapper.api.switch_to_parent.assert_not_called()


def test_latest_with_text_skips_empty_comments(hass):
    entity = sensor(hass, [grade(1, text="  "), grade(2, text="Praise", date=datetime(2026, 9, 20))])
    attrs = entity.extra_state_attributes
    assert attrs["latest"]["id"] == 1
    assert attrs["latest_with_text"]["id"] == 2
    assert len(attrs["assessments"]) == 2
    entity.coordinator.data["text_grades"] = [grade(text="")]
    assert entity.extra_state_attributes["latest_with_text"] is None


def test_latest_with_text_outside_exposed_list(hass):
    entity = sensor(hass, [grade(i, text="") for i in range(60)] + [grade(99, text="Old praise", date=None)])
    attrs = entity.extra_state_attributes
    assert attrs["assessments_exposed"] == 50
    assert attrs["latest_with_text"]["id"] == 99
    assert entity.state == 61


def test_subject_assessments_match_ids_and_preserve_grade_count(hass):
    from custom_components.homeassistantedupage.sensor import EduPageSubjectSensor
    overview = sensor(hass, [grade(1), grade(2)])
    overview.coordinator.data["text_grades"][1].subject_id = 8
    overview.coordinator.data["grades"] = []
    overview.coordinator.data["data_ok"]["grades"] = True
    entity = EduPageSubjectSensor(overview.coordinator, 123, "Student", "Behavior", "7")
    assert entity.state == 0
    attrs = entity.extra_state_attributes
    assert attrs["text_assessments_count"] == 1
    assert attrs["text_assessments_latest_with_text"]["id"] == 1
    assert attrs["text_assessments_data_stale"] is False
    overview.coordinator.data["text_grades"] = []
    overview.coordinator.data["data_ok"]["text_grades"] = False
    assert entity.extra_state_attributes["text_assessments_count"] == 1
    assert entity.extra_state_attributes["text_assessments_data_stale"] is True
    assert entity.extra_state_attributes["data_stale"] is False
    overview.coordinator.data["data_ok"]["text_grades"] = True
    assert entity.extra_state_attributes["text_assessments_count"] == 0


def test_subject_restores_text_attributes_independently(hass):
    from custom_components.homeassistantedupage.sensor import EduPageSubjectSensor
    overview = sensor(hass, [])
    overview.coordinator.data["data_ok"]["text_grades"] = False
    entity = EduPageSubjectSensor(overview.coordinator, 123, "Student", "Behavior", 7)
    entity._apply_restored(State("sensor.example", "unknown", {
        "text_assessments_count": 1,
        "text_assessments_latest_with_text": {"text": "Restored praise"},
    }))
    assert entity.extra_state_attributes["text_assessments_count"] == 1
    assert entity.extra_state_attributes["text_assessments_data_stale"] is True
