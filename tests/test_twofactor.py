"""Tests for the modern app-code two-factor driver (twofactor.py).

These cover the pure logic that does not require a running Home Assistant
instance: parsing the React ``props`` object, redirect URL resolution via
urljoin, and the valid/invalid/expired code submission path (with a mocked
session).
"""

import importlib.util
import json

from edupage_api.compression import RequestData
from edupage_api.exceptions import SecondFactorFailedException


def _load_twofactor():
    """Load twofactor.py standalone, skipping the HA-coupled package __init__."""
    path = (
        "custom_components/homeassistantedupage/twofactor.py"
    )
    spec = importlib.util.spec_from_file_location("twofactor_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


twofactor = _load_twofactor()


class _CookieJar:
    def __init__(self):
        self.cookies = {"PHPSESSID": "stub_session"}

    def get_dict(self, domain=None):
        return dict(self.cookies)


class _SessionStub:
    """Mocks the requests.Session the driver uses."""

    def __init__(self, rpc_post_result, redirect_response=None):
        self.rpc_post_result = rpc_post_result
        self.redirect_response = redirect_response or _Resp("<html>ok</html>")
        self.cookies = _CookieJar()
        self.posted_url = None
        self.posted_body = None
        self.requested_url = None

    def post(self, url, data, headers=None):
        self.posted_url = url
        self.posted_body = data
        return _Resp(self.rpc_post_result)

    def get(self, url):
        self.requested_url = url
        return self.redirect_response


class _ApiStub:
    """Test double for edupage_api.Edupage enough for finish_with_code."""

    def __init__(self, session):
        self.subdomain = "mshviezdoslavova1"
        self.username = "user"
        self.is_logged_in = False
        self.session = session
        self.reloaded = None


class _Resp:
    def __init__(self, text):
        self.text = text
        self.status_code = 200


class _LoginStub:
    """Replacement for edupage_api.Login used inside finish_with_code."""

    def __init__(self, api):
        self._api = api

    def reload_data(self, subdomain, phpsessid, username):
        self._api.is_logged_in = True


def _rpc(text):
    """A plain-JSON response body as EduPage would return (uncompressed)."""
    return text


def test_parse_props_minimal():
    html = 'var props = {"username":"u","requestid":"req123","tu":"t","gu":null,"au":""};'
    props = twofactor._extract_props(html)
    assert props == {
        "username": "u",
        "requestid": "req123",
        "tu": "t",
        "gu": None,
        "au": "",
    }


def test_parse_props_with_nested_json():
    html = 'var props = {"requestid":"r","nested":{"a":1,"b":[1,2]}};'
    props = twofactor._extract_props(html)
    assert props["requestid"] == "r"
    assert props["nested"] == {"a": 1, "b": [1, 2]}


def test_parse_props_returns_none_when_missing():
    assert twofactor._extract_props("<html>no props here</html>") is None


def test_finish_with_code_valid(monkeypatch):
    ok = _rpc(json.dumps({"status": "OK", "redirectUrl": "/user/"}))
    session = _SessionStub(ok)
    api = _ApiStub(session)
    monkeypatch.setattr(twofactor, "Login", _LoginStub)
    factor = twofactor.EdupageTwoFactor(
        api, requestid="r", tu="t", gu=None, au=""
    )
    factor.finish_with_code("123456")
    assert api.is_logged_in is True
    assert session.requested_url == f"https://{api.subdomain}.edupage.org/user/"


def test_finish_with_code_wrong_code():
    err = _rpc(
        json.dumps({"status": "ERR", "err": {"error_text": "Incorrect code"}})
    )
    session = _SessionStub(err)
    api = _ApiStub(session)
    factor = twofactor.EdupageTwoFactor(
        api, requestid="r", tu="t", gu=None, au=""
    )
    try:
        factor.finish_with_code("000000")
    except SecondFactorFailedException:
        pass
    else:
        raise AssertionError("expected SecondFactorFailedException")
    assert api.is_logged_in is False


def test_finish_with_code_expired_no_redirect():
    # "OK" status but no redirectUrl -> treated as an expired/failed code.
    ok_no_redirect = _rpc(json.dumps({"status": "OK"}))
    session = _SessionStub(ok_no_redirect)
    api = _ApiStub(session)
    factor = twofactor.EdupageTwoFactor(
        api, requestid="r", tu="t", gu=None, au=""
    )
    try:
        factor.finish_with_code("123456")
    except SecondFactorFailedException:
        pass
    else:
        raise AssertionError("expected SecondFactorFailedException")


def test_absolute_redirect_url_not_double_joined(monkeypatch):
    ok_abs = _rpc(
        json.dumps({"status": "OK", "redirectUrl": "https://x.example/user/"})
    )
    session = _SessionStub(ok_abs)
    api = _ApiStub(session)
    monkeypatch.setattr(twofactor, "Login", _LoginStub)
    twofactor.EdupageTwoFactor(api, "r", "t", None, "").finish_with_code("1")
    assert session.requested_url == "https://x.example/user/"


def test_code_submission_params_never_contains_password(monkeypatch):
    ok = _rpc(json.dumps({"status": "OK", "redirectUrl": "/user/"}))
    session = _SessionStub(ok)
    api = _ApiStub(session)
    monkeypatch.setattr(twofactor, "Login", _LoginStub)

    captured = {}

    def fake_encode(d):
        captured["params"] = json.loads(d["rpcparams"])
        return "fake-encoded"

    monkeypatch.setattr(twofactor.RequestData, "encode_request_body", fake_encode)
    twofactor.EdupageTwoFactor(api, "r", "t", None, "").finish_with_code("123456")
    params = captured["params"]
    # Challenge fields are present.
    assert params["t2fasec"] == "123456"
    assert params["2fform"] == "1"
    assert params["2fNoSave"] == "y"
    assert params["tu"] == "t"
    # The password must never be submitted with the code.
    assert "password" not in params
    assert "username" not in params