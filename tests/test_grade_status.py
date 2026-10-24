"""Completeness reporting must distinguish partial, unknown and stale reads."""
from types import SimpleNamespace
from custom_components.homeassistantedupage.grade_status import completeness_attributes


def test_grade_completeness():
    c = SimpleNamespace(data={"grade_reports": {"grades": {"received": 13, "skipped": 4}}})
    assert completeness_attributes(c) == {
        "grades_received_total": 13, "grades_skipped_total": 4, "grades_complete": False,
    }
    assert completeness_attributes(c, "term_P1")["grades_complete"] is None
    c.data["grade_reports"]["grades"]["skipped"] = 0
    assert completeness_attributes(c)["grades_complete"] is True
    c.data["data_ok"] = {"grades": False}
    assert completeness_attributes(c)["grades_complete"] is None
