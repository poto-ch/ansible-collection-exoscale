# Copyright (c) 2026, Jocelyn Thode
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

"""Credential and client resolution for the Exoscale collection."""

from __future__ import annotations

import configparser
import os
from typing import Any

try:
    from exoscale.api.exceptions import ExoscaleAPIException

    HAS_EXOSCALE = True
except ImportError:
    HAS_EXOSCALE = False
    ExoscaleAPIException = None

try:
    from requests.exceptions import RequestException
except ImportError:
    # requests is a hard dependency of the SDK, so this only happens with it.
    RequestException = None


class ExoscaleError(Exception):
    """A credential or client resolution problem, reported by the plugin."""


DEFAULT_ZONE = "ch-gva-2"
DEFAULT_PROFILE = "default"
CONFIG_FILES = ("~/.exoscale.ini", "./exoscale.ini")

# Network level failures: a zone failing this way is skipped unless the caller
# opted into fail_on_errors. Anything else is a bug and must surface.
FETCH_ERRORS = tuple(
    error for error in (ExoscaleAPIException, RequestException) if error is not None
)


def get_credentials(options):
    """Resolve the API key and secret.

    Each value is resolved on its own, from the plugin option, then the
    environment, then the INI file section named by I(api_profile).

    Returns a ``(key, secret)`` tuple.
    """
    key = options.get("api_key") or os.environ.get("EXOSCALE_API_KEY")
    secret = options.get("api_secret") or os.environ.get("EXOSCALE_API_SECRET")
    profile = (
        options.get("api_profile")
        or os.environ.get("EXOSCALE_PROFILE")
        or DEFAULT_PROFILE
    )

    if not (key and secret):
        path = options.get("api_config") or os.environ.get("EXOSCALE_CONFIG")
        section = _read_ini(path, profile)
        key = key or section.get("key")
        secret = secret or section.get("secret")

    if not (key and secret):
        raise ExoscaleError(
            "Missing Exoscale credentials. Set api_key/api_secret, the"
            " EXOSCALE_API_KEY and EXOSCALE_API_SECRET environment variables, or"
            f" the key and secret of the [{profile}] section in ~/.exoscale.ini."
        )

    return key, secret


def _client_class() -> Any:
    """The v2 Client, imported on use so a missing SDK reads as a plugin error.

    The class is assembled from the OpenAPI document when the SDK is imported,
    so its methods are invisible to type checkers.
    """
    try:
        from exoscale.api.v2 import Client
    except ImportError as error:
        raise ExoscaleError(
            "Could not import Python library exoscale, it is required to talk to"
            " the Exoscale API."
        ) from error

    return Client


def get_client(key, secret, zone=None, url=None, timeout=None):
    """Build a v2 client, scoped to a zone or to an explicit endpoint."""
    client_class = _client_class()

    if url and zone:
        raise ExoscaleError(
            "An Exoscale client takes either an endpoint or a zone, not both."
        )

    if url:
        client = client_class(key, secret, url=url)
    elif zone:
        client = client_class(key, secret, zone=zone)
    else:
        client = client_class(key, secret)

    if timeout:
        _apply_timeout(client.http_client, timeout)

    return client


def _read_ini(path, profile):
    """Return the key/secret pair of one INI section, empty when not found."""
    candidates = (path,) if path else CONFIG_FILES

    for candidate in candidates:
        expanded = os.path.expanduser(candidate)
        if not os.path.isfile(expanded):
            continue

        parser = configparser.ConfigParser()
        try:
            parser.read(expanded)
        except configparser.Error as error:
            raise ExoscaleError(
                f"Could not read the Exoscale configuration {expanded}: {error}"
            ) from error

        if parser.has_section(profile):
            return {
                name: parser.get(profile, name, fallback=None)
                for name in ("key", "secret")
            }

        if path:
            raise ExoscaleError(
                f"The Exoscale configuration {expanded} has no [{profile}] section."
            )

    if path:
        raise ExoscaleError(
            f"The Exoscale configuration {os.path.expanduser(path)} does not exist."
        )

    return {}


def _apply_timeout(session, timeout):
    """Bind a default timeout on a requests session.

    ``requests`` has no session level default, and the SDK sends ``list-zones``
    through module level ``requests`` anyway, so this only covers the
    authenticated calls.
    """
    request = session.request

    def request_with_timeout(*args, **kwargs):
        kwargs.setdefault("timeout", timeout)
        return request(*args, **kwargs)

    session.request = request_with_timeout
