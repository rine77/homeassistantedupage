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


async def test_failed_coordinator_refresh_makes_retained_completeness_unknown(hass):
    """A retained good snapshot must not claim the failed refresh is complete."""
    import logging
    from unittest.mock import AsyncMock

    from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

    snapshot = {
        "grade_reports": {
            "grades": {"received": 13, "skipped": 0},
            "term_P1": {"received": 13, "skipped": 0},
        },
        "data_ok": {"grades": True, "grades_per_term": True},
    }
    update = AsyncMock(return_value=snapshot)
    coordinator = DataUpdateCoordinator(
        hass, logging.getLogger(__name__), name="grade completeness",
        update_method=update, config_entry=None,
    )
    await coordinator.async_refresh()
    assert coordinator.last_update_success
    for key in ("grades", "term_P1"):
        assert completeness_attributes(coordinator, key)["grades_complete"] is True

    update.side_effect = UpdateFailed("coordinator refresh failed")
    await coordinator.async_refresh()
    assert not coordinator.last_update_success
    assert coordinator.data is snapshot
    for key in ("grades", "term_P1"):
        attrs = completeness_attributes(coordinator, key)
        assert attrs["grades_complete"] is None
        assert attrs["grades_received_total"] == 13
        assert attrs["grades_skipped_total"] == 0

    update.side_effect = None
    await coordinator.async_refresh()
    assert coordinator.last_update_success
    for key in ("grades", "term_P1"):
        assert completeness_attributes(coordinator, key)["grades_complete"] is True
