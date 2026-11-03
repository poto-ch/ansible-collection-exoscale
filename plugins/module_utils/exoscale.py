# Copyright (c) 2026, Jocelyn Thode
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

"""Credential and client resolution for the Exoscale collection."""

from __future__ import annotations

import os
import tomllib
from typing import Any, NamedTuple

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


class Credentials(NamedTuple):
    key: str
    secret: str
    endpoint: str | None = None
    account: str | None = None


DEFAULT_ZONE = "ch-gva-2"

# Where `exo config` writes the CLI configuration, in lookup order.
CONFIG_FILES = (
    "~/.config/exoscale/exoscale.toml",
    "~/Library/Application Support/exoscale/exoscale.toml",
    "~/.exoscale/exoscale.toml",
)

# Network level failures: a zone failing this way is skipped unless the caller
# opted into fail_on_errors. Anything else is a bug and must surface.
FETCH_ERRORS = tuple(
    error for error in (ExoscaleAPIException, RequestException) if error is not None
)


def get_credentials(options, run=None):
    """Resolve the API key, secret, endpoint and CLI account name.

    Each value is resolved on its own: the plugin option, then the environment,
    then the account selected in the exo CLI configuration by I(api_account),
    C(EXOSCALE_ACCOUNT) or the file's C(defaultaccount).

    ``run`` executes an account's C(secretCommand) and returns what it printed.
    Module code must pass its own (C(module.run_command)); without one a
    C(secretCommand) account is an error rather than a silent subprocess.
    """
    key = options.get("api_key") or os.environ.get("EXOSCALE_API_KEY")
    secret = options.get("api_secret") or os.environ.get("EXOSCALE_API_SECRET")
    endpoint = None
    account = None

    if not (key and secret):
        entry = _read_account(options)
        key = key or entry.get("key")
        secret = secret or _account_secret(entry, run)
        endpoint = entry.get("endpoint")
        account = entry.get("name")

    if not (key and secret):
        raise ExoscaleError(
            "Missing Exoscale credentials. Set api_key/api_secret, the"
            " EXOSCALE_API_KEY and EXOSCALE_API_SECRET environment variables, or"
            " the key and secret of a CLI account (see `exo config`)."
        )

    return Credentials(key, secret, endpoint, account)


def _read_account(options):
    """Return the selected account of the exo CLI configuration."""
    path = options.get("api_config") or os.environ.get("EXOSCALE_CONFIG")

    if path:
        expanded = os.path.expanduser(path)
        if not os.path.isfile(expanded):
            raise ExoscaleError(
                f"The Exoscale CLI configuration {expanded} does not exist."
            )
        candidates = (expanded,)
    else:
        candidates = CONFIG_FILES

    for candidate in candidates:
        expanded = os.path.expanduser(candidate)
        if not os.path.isfile(expanded):
            continue

        try:
            with open(expanded, "rb") as config_file:
                config = tomllib.load(config_file)
        except (OSError, tomllib.TOMLDecodeError) as error:
            raise ExoscaleError(
                f"Could not read the Exoscale CLI configuration {expanded}: {error}"
            ) from error

        return _select_account(config, expanded, options)

    raise ExoscaleError(
        "No Exoscale CLI configuration found. Create one with `exo config`, set"
        " api_config, or pass api_key and api_secret."
    )


def _select_account(config, path, options):
    accounts = config.get("accounts") or []
    if not isinstance(accounts, list):
        raise ExoscaleError(
            f"The accounts of {path} must be an array of tables, as `exo config`"
            " writes them."
        )
    name = (
        options.get("api_account")
        or os.environ.get("EXOSCALE_ACCOUNT")
        or config.get("defaultaccount")
    )

    if name is None:
        if len(accounts) == 1:
            return accounts[0]
        if not accounts:
            raise ExoscaleError(
                f"The Exoscale CLI configuration {path} has no accounts."
            )
        raise ExoscaleError(
            f"Set api_account or EXOSCALE_ACCOUNT, {path} has {len(accounts)} accounts."
        )

    for account in accounts:
        if account.get("name") == name:
            return account

    raise ExoscaleError(f"The account {name!r} does not exist in {path}.")


def _account_secret(account, run):
    """The account secret, from `secret` or the `secretCommand` it names.

    ``run`` is the caller's command runner: it returns the printed secret and
    raises ExoscaleError itself when the command fails.
    """
    if account.get("secret"):
        return account["secret"]

    command = account.get("secretCommand")
    if not command:
        return None
    if not isinstance(command, list):
        raise ExoscaleError(
            f"The secretCommand of the account {account.get('name')!r} must be a"
            " list of arguments."
        )

    if run is None:
        raise ExoscaleError(
            f"The account {account.get('name')!r} uses a secretCommand, which this"
            " context cannot run. Set api_secret, or the account's secret."
        )

    return run(command).strip() or None


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
