"""Detect whether a Moodle site is fronted by external SSO (Microsoft / Google /
SAML), so the UI can steer the user straight to the browser token flow instead of
letting username/password fail with a generic "Invalid login".

We deliberately do NOT try to replay the institutional SSO login headlessly: the
Azure-AD "convergence" SAML flow (and MFA, conditional access, CAPTCHAs) make that
brittle and unsafe. Instead, the user signs in through their real browser via
Moodle's mobile launch endpoint and pastes back the resulting token — see
:func:`app.imports.moodle_api.decode_launch_token`.
"""
from __future__ import annotations

from typing import Callable, Optional
from urllib.parse import urlparse

from .moodle_api import build_launch_url, normalize_base_url

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# External identity-provider hosts a Moodle mobile launch may redirect to.
_SSO_HOSTS = (
    "login.microsoftonline.com", "login.microsoft.com", "sts.",
    "accounts.google.com", "adfs.", "shibboleth", "idp.", "sso.",
    "okta.com", "auth0.com", "login.live.com",
)


def detect_sso(base_url: str, *, session_factory: Optional[Callable] = None,
               timeout: int = 15) -> Optional[str]:
    """Return the SSO provider host if the Moodle mobile launch redirects off-site
    to an external identity provider, else None (token grant likely works) or None
    on any network error (caller treats unknown as "not detected")."""
    import requests
    try:
        base = normalize_base_url(base_url)
    except Exception:
        return None
    sess = (session_factory or requests.Session)()
    sess.headers["User-Agent"] = _UA
    try:
        r = sess.get(build_launch_url(base), timeout=timeout)
    except Exception:
        return None
    host = (urlparse(r.url or "").hostname or "").lower()
    if host and any(s in host for s in _SSO_HOSTS):
        return host
    return None
