"""Regression tests for array-shaped `dbi` item groups and session reloading.

EduPage serialises a ``dbi`` item group that has no entries as an empty JSON
array instead of an empty object, while ``edupage_api``'s ``DbiHelper`` uses
mapping access on the group. ``edupage-api`` 0.13.3 reads an empty array as an
empty mapping, so this is handled by the library and this integration carries no
workaround of its own.

These tests exercise the real library rather than mocking it, so a regression in
the dependency is caught here rather than on a user's install.
"""

from edupage_api import Edupage
from edupage_api.dbi import DbiHelper


def _api(dbi):
    """A logged-in edupage_api client with the given dbi payload."""
    api = Edupage()
    api.is_logged_in = True
    api.subdomain = "example"
    api.data = {"dbi": dbi}
    return api


def test_empty_group_is_read_as_empty_mapping():
    """The id lookup that used to raise now returns None."""
    api = _api({"classrooms": [], "teachers": {}, "subjects": []})

    assert DbiHelper(api).fetch_classroom_number("42") is None
    assert DbiHelper(api).fetch_subject_name("9") is None
    assert DbiHelper(api).fetch_classroom_list() == {}


def test_get_classes_works_with_empty_groups():
    """get_classes() resolves teachers through fetch_classroom_number()."""
    api = _api(
        {
            "classes": {
                "-2": {
                    "id": -2,
                    "name": "Class name",
                    "short": "CN",
                    "grade": 5,
                    "classroomid": "42",
                    "teacherid": "7",
                    "teacher2id": "",
                }
            },
            "classrooms": [],
            "teachers": {
                # classroomid marks the record as a teacher; its lookup goes
                # through fetch_classroom_number(), i.e. the empty group.
                "7": {
                    "id": 7,
                    "firstname": "A",
                    "lastname": "B",
                    "classroomid": "42",
                },
            },
            "subjects": [],
        }
    )

    classes = api.get_classes()

    assert [c.short for c in classes] == ["CN"]


def test_populated_group_is_still_returned_unchanged():
    """A group that is an object is untouched."""
    classes = {"-2": {"id": -2, "short": "A"}}
    api = _api({"classes": classes})

    assert DbiHelper(api).fetch_class_list() is classes
    assert DbiHelper(api).fetch_class_name("-2") == "A"


def test_integration_carries_no_dbi_workaround():
    """The normalisation helper was removed once the library shipped the fix."""
    from custom_components.homeassistantedupage import config_flow
    from custom_components.homeassistantedupage import homeassistant_edupage

    assert not hasattr(homeassistant_edupage, "_normalise_dbi_groups")
    assert not hasattr(config_flow, "_normalise_dbi_groups")


def test_load_session_uses_login_session(monkeypatch):
    """reload_data() moved from Login to LoginSession in edupage-api 0.13.1."""
    from custom_components.homeassistantedupage.homeassistant_edupage import (
        Edupage as Wrapper,
    )

    seen = {}

    class _FakeLoginSession:
        def __init__(self, target):
            self._target = target

        def reload_data(self, subdomain, session_id, username):
            seen["args"] = (subdomain, session_id, username)
            # EduPage returns an empty array for a group with no entries.
            self._target.data = {"dbi": {"classrooms": [], "subjects": []}}

    monkeypatch.setattr(
        "custom_components.homeassistantedupage.homeassistant_edupage.LoginSession",
        _FakeLoginSession,
    )

    wrapper = Wrapper.__new__(Wrapper)  # no hass needed for the sync path
    wrapper.api = Edupage()  # the edupage_api client
    wrapper._load_session("sub", "sess", "user")

    assert seen["args"] == ("sub", "sess", "user")
    # Readable by the real library, with no normalisation on our side.
    assert DbiHelper(wrapper.api).fetch_classroom_number("42") is None