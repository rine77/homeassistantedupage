"""Modern app-code two-factor authentication for EduPage.

The installed `edupage-api==0.12.5` library implements the *old* two-factor
flow: it parses the hidden `<input value="...">` fields (`csrfauth`, `au`,
`gu`) from the 2FA page HTML (`Login._extract_two_factor_fields`). Schools that
rolled out the newer React login page no longer render those hidden inputs —
the challenge is embedded in a JS `props` variable and the code is submitted
via the same JSON-RPC `login` endpoint the library already uses.

As a result `api.login()` raises
`BadCredentialsException("EduPage did not provide two-factor fields")` for any
2FA-protected account on such schools, before the integration's TOTP page is
ever shown.

This module is a self-contained, minimal driver that replaces only the broken
2FA part of the flow, so the config flow keeps its existing "enter the
confirmation code" page. It reuses `edupage-api` for the JSON-RPC wire format
(`RequestData`) and for reloading the resulting session (`Login.reload_data`),
so the installed library is never patched.
"""

import json
import logging

from urllib.parse import urljoin

from edupage_api import Login
from edupage_api.compression import RequestData
from edupage_api.exceptions import (
    BadCredentialsException,
    SecondFactorFailedException,
)

_LOGGER = logging.getLogger(__name__)

_RPC_URL = "/login/?cmd=MainLogin&akcia=login"


class EdupageTwoFactor:
    """A pending mobile-app-code 2FA challenge for an EduPage account."""

    def __init__(self, api, requestid, tu, gu, au):
        self.api = api
        self.requestid = requestid
        self.tu = tu
        self.gu = gu
        self.au = au

    def finish_with_code(self, code: str):
        """Submit the 6-digit code and finalise the login on `self.api`.

        Mirrors the browser's `submitSecondFactor`: the code is sent via the
        same JSON-RPC `login` endpoint, carrying only the challenge fields
        (`t2fasec`, `2fNoSave`, `2fform`, `tu`, `gu`, `au`). The password is
        deliberately *not* resent here and never persisted on the session —
        the server keeps the login state from the initial RPC stage.
        """
        base_url = f"https://{self.api.subdomain}.edupage.org"
        headers = {"Content-Type": "application/x-www-form-urlencoded"}

        params = {
            "tu": self.tu,
            "gu": self.gu,
            "au": self.au,
            "t2fasec": code,
            "2fNoSave": "y",
            "2fform": "1",
        }

        response = self.api.session.post(
            f"{base_url}{_RPC_URL}",
            data=RequestData.encode_request_body(
                {"rpcparams": json.dumps(params)}
            ),
            headers=headers,
        )

        parsed = _parse_rpc_response(response.text) or {}
        status = parsed.get("status")
        if status != "OK" or not parsed.get("redirectUrl"):
            error = (parsed.get("err") or {}).get("error_text") or str(parsed)
            raise SecondFactorFailedException(
                f"Second factor failed! (wrong/expired code?): {error}"
            )

        redirect_url = parsed.get("redirectUrl")

        final = self.api.session.get(urljoin(base_url, redirect_url or "/user/"))

        cookies = self.api.session.cookies.get_dict(
            f"{self.api.subdomain}.edupage.org"
        )
        phpsess = cookies.get("PHPSESSID")
        if not phpsess:
            raise SecondFactorFailedException(
                "Second factor did not yield a session (no PHPSESSID)."
            )

        Login(self.api).reload_data(
            self.api.subdomain, phpsess, self.api.username
        )

        if not self.api.is_logged_in:
            raise SecondFactorFailedException(
                "Second factor completed but the session could not be loaded."
            )


def _parse_rpc_response(text: str):
    """Decode an EduPage JSON-RPC response (compressed `eqz:`/`dz:` body)."""
    if not text:
        return None
    try:
        return json.loads(RequestData.decode_response(text))
    except Exception:  # noqa: BLE001
        _LOGGER.debug("EduPage RPC response did not decode: %s", text[:200])
        return None


def _extract_props(html: str):
    """Parse the `var props = {...};` embedded in the modern 2FA page.

    Returns a dict or None. The page serves a React component whose props are
    assigned to a JS variable, e.g.:
        var props = {"username":"...","requestid":"...","gu":null,"au":"",...};
    """
    marker = "var props"
    idx = html.find(marker)
    if idx == -1:
        return None
    json_start = html.find("{", idx)
    if json_start == -1:
        return None

    depth = 0
    in_string = False
    escape = False
    for i in range(json_start, len(html)):
        ch = html[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(html[json_start : i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def start_two_factor(api, username, password, subdomain):
    """Perform the RPC password login, land on the 2FA page and read its props.

    Returns an `EdupageTwoFactor` challenge, or raises `BadCredentialsException`
    if login/recovery of the challenge fails.

    This mirrors `edupage-api` `Login.__login_with_rpc` but reads the challenge
    from the JS `props` object instead of hidden `<input>` fields.
    """
    base_url = f"https://{subdomain}.edupage.org"
    headers = {"Content-Type": "application/x-www-form-urlencoded"}

    token_resp = api.session.post(
        f"{base_url}/login/?cmd=MainLogin&akcia=getToken",
        data=RequestData.encode_request_body(
            {"rpcparams": json.dumps({"username": username, "edupage": ""})}
        ),
        headers=headers,
    )
    if token_resp.status_code != 200:
        raise BadCredentialsException("EduPage did not return a login token")
    token_data = _parse_rpc_response(token_resp.text)
    token = token_data.get("token") if token_data else None
    if not token:
        raise BadCredentialsException("EduPage did not provide a login token")

    api.session.get(f"{base_url}/login/?cmd=MainLogin")

    login_resp = api.session.post(
        f"{base_url}{_RPC_URL}",
        data=RequestData.encode_request_body(
            {
                "rpcparams": json.dumps(
                    {
                        "username": username,
                        "password": password,
                        "userToken": token,
                        "edupage": "",
                        "ctxt": "",
                        "tu": None,
                        "gu": None,
                        "au": None,
                    }
                )
            }
        ),
        headers=headers,
    )
    login_data = _parse_rpc_response(login_resp.text) or {}
    error_id = (login_data.get("err") or {}).get("error_id")
    redirect_url = login_data.get("redirectUrl")

    if error_id == "invalid_token":
        raise BadCredentialsException("EduPage rejected the login token")
    if not redirect_url:
        raise BadCredentialsException("EduPage did not redirect to two-factor")

    page = api.session.get(urljoin(base_url, redirect_url))

    props = _extract_props(page.text)
    if not props:
        raise BadCredentialsException(
            "EduPage did not provide two-factor fields (unrecognised 2FA page)"
        )

    api.subdomain = subdomain
    api.username = username

    return EdupageTwoFactor(
        api,
        requestid=props.get("requestid"),
        tu=props.get("tu"),
        gu=props.get("gu"),
        au=props.get("au"),
    )
