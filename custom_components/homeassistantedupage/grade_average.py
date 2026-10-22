"""Optional school-specific letter conversion and weighted grade averages."""

import math

CONF_LETTER_GRADES = "convert_letter_grades"
DEFAULT_LETTER_MAPPING = dict(zip("ABCDEF", (1, 2, 3, 4, 5, 5)))


def mapping_from_options(options):
    if not options.get(CONF_LETTER_GRADES, False):
        return None
    return {
        letter: float(options.get(f"letter_grade_{letter.lower()}", value))
        for letter, value in DEFAULT_LETTER_MAPPING.items()
    }


def average_summary(grades, mapping=None):
    total = weight_sum = 0.0
    converted = False
    for grade in grades:
        raw = getattr(grade, "grade_n", None)
        if raw is None or isinstance(raw, bool):
            continue
        letter = str(raw).strip().upper()
        is_converted = mapping is not None and letter in mapping
        try:
            value = float(
                mapping[letter] if is_converted else str(raw).replace(",", ".")
            )
            weight = getattr(grade, "importance", None)
            weight = 1.0 if weight is None else float(weight)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(value) or not math.isfinite(weight) or weight <= 0:
            continue
        total += value * weight
        weight_sum += weight
        converted |= is_converted
    return {
        "grade_average": round(total / weight_sum, 2) if weight_sum else None,
        "grade_average_converted": converted,
        "grade_average_method": "weighted",
    }


def coordinator_mapping(coordinator):
    entry = getattr(coordinator, "config_entry", None)
    return mapping_from_options(getattr(entry, "options", {}) or {})


def completeness_attributes(coordinator, key="grades"):
    report = (coordinator.data or {}).get("grade_reports", {}).get(key)
    # Unknown is different from a verified zero skipped grades.
    skipped = report.get("skipped") if isinstance(report, dict) else None
    source = "grades" if key == "grades" else "grades_per_term"
    fresh = (coordinator.data or {}).get("data_ok", {}).get(source, True)
    return {
        "grades_skipped_total": skipped,
        "grades_received_total": report.get("received")
        if isinstance(report, dict)
        else None,
        "grades_complete": skipped == 0 if fresh and skipped is not None else None,
    }
