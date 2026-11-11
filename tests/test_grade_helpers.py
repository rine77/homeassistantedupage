"""Tests for grade-to-timeline linkage helpers."""

from types import SimpleNamespace

from custom_components.homeassistantedupage.grade_helpers import (
    grade_reference_ids,
    matching_grade,
)


def test_nested_grade_reference_is_found_below_dynamic_key():
    """Real EduPage grade payloads expose udalostid inside a nested list."""
    event = SimpleNamespace(
        event_id=900,
        additional_data={"-19": [{"udalostid": "21", "znamkaid": 4}]},
    )

    assert grade_reference_ids(event) == {"21"}


def test_embedded_reference_precedes_direct_timeline_id():
    """The school-event reference wins when both ID namespaces are present."""
    event = SimpleNamespace(
        event_id=900,
        additional_data={"-19": [{"udalostid": "21"}]},
    )
    embedded = SimpleNamespace(event_id=21)
    direct = SimpleNamespace(event_id=900)

    assert matching_grade(event, [direct, embedded]) is embedded


def test_direct_timeline_id_remains_a_fallback():
    """Older or different EduPage payloads can still match directly."""
    event = SimpleNamespace(event_id="21", additional_data={})
    grade = SimpleNamespace(event_id=21)

    assert matching_grade(event, [grade]) is grade


def test_linkage_traversal_is_bounded_and_ignores_unrelated_values():
    """Unexpected nested data neither leaks nor produces false references."""
    value = {"other": "private value"}
    for _ in range(10):
        value = {"nested": [value]}
    event = SimpleNamespace(event_id=900, additional_data=value)

    assert grade_reference_ids(event) == set()
    assert matching_grade(event, [SimpleNamespace(event_id=21)]) is None


def test_subject_audit_distinguishes_mapping_from_options():
    from custom_components.homeassistantedupage.grade_helpers import grade_subject_audit

    subjects = [SimpleNamespace(subject_id=1), SimpleNamespace(subject_id="2")]
    grades = [
        SimpleNamespace(event_id=10, subject_id="1", grade_n="SECRET"),
        SimpleNamespace(event_id=11, subject_id=2),
        SimpleNamespace(event_id=12, subject_id=-1),
    ]
    result = grade_subject_audit(grades, subjects, [1])
    assert result == {
        "received": 3,
        "assigned": 2,
        "selected": 1,
        "unassigned": [(12, -1)],
        "excluded_by_options": [(11, 2)],
    }
    assert "SECRET" not in repr(result)
    assert grade_subject_audit(grades, subjects, [])["selected"] == 0
    assert grade_subject_audit(grades, subjects, None)["selected"] == 2
