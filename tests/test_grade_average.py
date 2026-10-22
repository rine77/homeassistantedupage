"""School letter mapping, weights, exclusions and completeness."""

from types import SimpleNamespace

from custom_components.homeassistantedupage.grade_average import (
    average_summary,
    completeness_attributes,
    mapping_from_options,
)


def grade(value, weight=None):
    return SimpleNamespace(grade_n=value, importance=weight)


def test_letter_conversion_is_opt_in_and_configurable():
    grades = [grade("A", 2), grade("C", 1), grade("F", 1), grade("(NESPLNĚNO)", 100)]
    assert mapping_from_options({}) is None
    assert average_summary(grades)["grade_average"] is None
    result = average_summary(
        grades, mapping_from_options({"convert_letter_grades": True})
    )
    assert result == {
        "grade_average": 2.5,
        "grade_average_converted": True,
        "grade_average_method": "weighted",
    }
    custom = mapping_from_options({"convert_letter_grades": True, "letter_grade_a": 2})
    assert average_summary([grade("A")], custom)["grade_average"] == 2


def test_invalid_values_and_zero_weights_do_not_poison_average():
    grades = [
        grade(1, 2),
        grade(4),
        grade(5, 0),
        grade(100, -2),
        grade("nan"),
        grade(5, "inf"),
        grade(True),
    ]
    assert average_summary(grades)["grade_average"] == 2
    assert average_summary([grade("(NESPLNĚNO)")])["grade_average"] is None


def test_complete_incomplete_and_unknown_reports():
    coordinator = SimpleNamespace(
        data={"grade_reports": {"grades": {"received": 13, "skipped": 4}}}
    )
    assert completeness_attributes(coordinator) == {
        "grades_received_total": 13,
        "grades_skipped_total": 4,
        "grades_complete": False,
    }
    assert (
        completeness_attributes(coordinator, "term_P1")["grades_skipped_total"] is None
    )
