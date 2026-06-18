"""Offline tests for SSO detection (app/imports/moodle_sso.py).

The real detection makes one HTTP GET to the Moodle mobile launch endpoint and
inspects the final redirect host. We inject a fake session so no network is hit.
"""
from __future__ import annotations

from app.imports import moodle_sso


class _FakeResp:
    def __init__(self, url):
        self.url = url


class _FakeSession:
    """Minimal stand-in for requests.Session that returns a preset final URL."""
    def __init__(self, final_url):
        self._final = final_url
        self.headers = {}

    def get(self, url, timeout=0):
        return _FakeResp(self._final)


def _factory(final_url):
    return lambda: _FakeSession(final_url)


def test_detect_sso_microsoft():
    host = moodle_sso.detect_sso(
        "https://elearn.uni.edu",
        session_factory=_factory("https://login.microsoftonline.com/abc/saml2?x=1"))
    assert host == "login.microsoftonline.com"


def test_detect_sso_google():
    host = moodle_sso.detect_sso(
        "https://moodle.uni.edu",
        session_factory=_factory("https://accounts.google.com/o/saml2/idp?x"))
    assert host == "accounts.google.com"


def test_detect_sso_none_for_local_login():
    # Stays on the Moodle host -> token grant is plausible, not SSO.
    host = moodle_sso.detect_sso(
        "https://moodle.uni.edu",
        session_factory=_factory("https://moodle.uni.edu/login/index.php"))
    assert host is None


def test_detect_sso_network_error_is_none():
    class _BoomSession:
        headers = {}
        def get(self, *a, **k):
            raise OSError("dns fail")
    host = moodle_sso.detect_sso("https://x.edu", session_factory=lambda: _BoomSession())
    assert host is None
