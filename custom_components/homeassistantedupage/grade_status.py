"""Grade-response completeness, independent of average calculation."""

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
