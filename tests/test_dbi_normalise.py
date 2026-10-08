"""Tests for normalisation of empty array-shaped EduPage dbi item groups.

EduPage serialises a `dbi` item group that has no entries as an empty JSON array
instead of an empty object. ``edupage_api``'s ``DbiHelper`` calls ``.get()`` on the
group, so an array-shaped group raises ``AttributeError: 'list' object has no
attribute 'get'`` and breaks ``get_classes()`` / ``get_timetable()``.

The workaround reads an *empty* array as an empty mapping and leaves everything
else untouched - in particular a populated array must keep raising rather than be
reinterpreted with fabricated positional ids.
"""

from types import SimpleNamespace

from custom_components.homeassistantedupage.homeassistant_edupage import (
    _normalise_dbi_groups,
)


def _api(data):
    """Build a stand-in for the edupage-api Edupage object."""
    return SimpleNamespace(data=data)


def test_empty_group_is_rewritten_as_empty_mapping():
    """An empty array group becomes an empty mapping."""
    api = _api({"dbi": {"classrooms": [], "teachers": [], "subjects": []}})

    _normalise_dbi_groups(api)

    assert api.data["dbi"]["classrooms"] == {}
    assert api.data["dbi"]["teachers"] == {}
    assert api.data["dbi"]["subjects"] == {}


def test_populated_groups_are_left_untouched():
    """Groups that are already objects are returned unchanged, by identity."""
    classes = {"-2": {"id": -2, "name": "1.A", "short": "1.A"}}
    dayparts = {"h1": {"id": 1, "starttime": "08:00"}}
    api = _api({"dbi": {"classes": classes, "dayparts": dayparts}})

    _normalise_dbi_groups(api)

    assert api.data["dbi"]["classes"] is classes
    assert api.data["dbi"]["dayparts"] is dayparts


def test_empty_array_nested_inside_a_group_is_not_rewritten():
    """Only top-level group values are rewritten; nested data is left alone."""
    event_types = {"lesson": {"id": 1, "sub": []}}
    api = _api({"dbi": {"event_types": event_types}})

    _normalise_dbi_groups(api)

    assert api.data["dbi"]["event_types"] is event_types
    assert api.data["dbi"]["event_types"]["lesson"]["sub"] == []


def test_populated_array_is_not_reinterpreted():
    """A populated array is left as-is so it keeps failing loudly.

    Mapping it onto positional keys would fabricate ids that can never match a
    real reference, turning a loud error into silent data loss.
    """
    populated = [{"name": "R1", "short": "R1"}]
    api = _api({"dbi": {"classrooms": populated}})

    _normalise_dbi_groups(api)

    assert api.data["dbi"]["classrooms"] is populated


def test_missing_or_malformed_payload_is_ignored():
    """Nothing to do, and no exception, when the payload is absent or odd."""
    for data in (
        None,
        {},
        {"dbi": None},
        {"dbi": []},
        {"dbi": "not-a-mapping"},
    ):
        api = _api(data)
        _normalise_dbi_groups(api)  # must not raise
        assert api.data == data


def test_api_without_data_attribute_is_ignored():
    """A bare object without ``data`` is tolerated."""
    _normalise_dbi_groups(SimpleNamespace())  # must not raise


def test_normalisation_is_idempotent():
    """Running twice is a no-op the second time."""
    api = _api({"dbi": {"classrooms": [], "classes": {"-2": {"id": -2}}}})

    _normalise_dbi_groups(api)
    after_first = {k: type(v).__name__ for k, v in api.data["dbi"].items()}
    _normalise_dbi_groups(api)
    after_second = {k: type(v).__name__ for k, v in api.data["dbi"].items()}

    assert after_first == after_second


def test_empty_array_group_no_longer_raises_in_the_library():
    """End-to-end: the real library reads the group through mapping access."""
    from edupage_api import Edupage
    from edupage_api.dbi import DbiHelper

    api = Edupage()
    api.is_logged_in = True
    api.subdomain = "example"
    api.data = {"dbi": {"classrooms": [], "teachers": {}, "subjects": []}}

    _normalise_dbi_groups(api)

    # __get_item_with_id() calls .get() on the group - this would raise without
    # normalisation.
    assert DbiHelper(api).fetch_classroom_number("42") is None
    assert DbiHelper(api).fetch_classroom_list() == {}


def test_runtime_session_load_normalises_the_payload(monkeypatch):
    """The runtime path normalises right after the stored session is reloaded.

    This is the path the coordinator uses on every poll and on setup.
    """
    from custom_components.homeassistantedupage.homeassistant_edupage import Edupage

    api = SimpleNamespace(data=None, is_logged_in=True)

    class _FakeLogin:
        """Stands in for edupage_api.Login and repopulates the payload."""

        def __init__(self, target):
            self._target = target

        def reload_data(self, subdomain, sessionid, username):
            self._target.data = {"dbi": {"classrooms": [], "teachers": {}}}

    monkeypatch.setattr(
        "custom_components.homeassistantedupage.homeassistant_edupage.Login", _FakeLogin
    )

    wrapper = Edupage.__new__(Edupage)  # no hass needed for the sync path
    wrapper.api = api
    Edupage._load_session(wrapper, "sub", "sess", "user")

    assert api.data["dbi"]["classrooms"] == {}
    assert api.data["dbi"]["teachers"] == {}
