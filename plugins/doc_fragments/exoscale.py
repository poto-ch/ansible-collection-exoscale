# Copyright (c) 2026, Jocelyn Thode
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

"""Exoscale API credentials, shared by the inventory plugin and future modules."""

from __future__ import annotations


class ModuleDocFragment:
    DOCUMENTATION = r"""
options:
  api_key:
    description:
      - Exoscale API key.
      - Takes precedence over the C(EXOSCALE_API_KEY) environment variable and
        over the CLI configuration.
    type: str
    env:
      - name: EXOSCALE_API_KEY
  api_secret:
    description:
      - Exoscale API secret.
      - Takes precedence over the C(EXOSCALE_API_SECRET) environment variable and
        over the CLI configuration.
    type: str
    env:
      - name: EXOSCALE_API_SECRET
  api_config:
    description:
      - Path to the C(exoscale.toml) written by C(exo config).
      - Defaults to the CLI location for the platform, for example
        C(~/.config/exoscale/exoscale.toml) on Linux.
    type: str
    env:
      - name: EXOSCALE_CONFIG
  api_account:
    description:
      - Account of the CLI configuration to read the credentials from.
      - Defaults to the C(EXOSCALE_ACCOUNT) environment variable, then to the
        C(defaultaccount) of the configuration file.
    type: str
    env:
      - name: EXOSCALE_ACCOUNT
  api_timeout:
    description:
      - Timeout in seconds for API requests.
      - Not C(EXOSCALE_TIMEOUT), which the CLI reads as a duration such as C(15s).
    type: int
    default: 30
    env:
      - name: EXOSCALE_INVENTORY_TIMEOUT
"""
