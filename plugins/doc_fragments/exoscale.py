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
        over I(api_config).
    type: str
    env:
      - name: EXOSCALE_API_KEY
  api_secret:
    description:
      - Exoscale API secret.
      - Takes precedence over the C(EXOSCALE_API_SECRET) environment variable and
        over I(api_config).
    type: str
    env:
      - name: EXOSCALE_API_SECRET
  api_config:
    description:
      - Path to the INI file holding the credentials.
      - Defaults to the first of C(~/.exoscale.ini) and C(./exoscale.ini) that
        exists.
    type: str
    env:
      - name: EXOSCALE_CONFIG
  api_profile:
    description:
      - INI file section holding the credentials.
    type: str
    default: default
    env:
      - name: EXOSCALE_PROFILE
  api_timeout:
    description:
      - Timeout in seconds for API requests.
    type: int
    default: 30
    env:
      - name: EXOSCALE_TIMEOUT
"""
