# Copyright (c) 2026, Jocelyn Thode
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

"""Exoscale Compute inventory source."""

from __future__ import annotations

DOCUMENTATION = r"""
name: exoscale
author: Jocelyn Thode (@jocelynthode)
short_description: Exoscale Compute inventory source
description:
  - Build an inventory of Exoscale Compute instances.
  - Instances are read from every zone in I(zones), or from every zone the API
    reports when I(zones) is empty.
  - This plugin reads its configuration from a file named C(exoscale.yaml) or
    C(exoscale.yml).
options:
  plugin:
    description:
      - Token which marks a configuration file as valid input for this plugin.
    type: str
    required: true
    choices: ['exoscale', 'poto.exoscale.exoscale']
  zones:
    description:
      - Zones to query.
      - Defaults to every zone returned by C(list-zones), which needs a client in
        the SDK default zone C(ch-gva-2).
    type: list
    elements: str
    default: []
  api_endpoint:
    description:
      - API endpoint to use instead of the per-zone
        C(https://api-{zone}.exoscale.com/v2).
      - C({zone}) is replaced with the zone being queried, for example
        C(https://exoscale-proxy.example/v2/{zone}).
      - Requires a non-empty I(zones), as zones cannot be discovered through an
        explicit endpoint.
    type: str
  filters:
    description:
      - Server-side filters forwarded to C(list-instances).
      - Supported keys are C(manager_id), C(manager_type), C(ip_address) and
        C(labels).
    type: dict
    default: {}
  accounts:
    description:
      - Read several Exoscale accounts into one inventory.
      - Every entry selects a CLI account and may override I(zones) and
        I(filters) for it.
      - Hosts are tagged with C(exoscale_account) and grouped under the account
        name.
    type: list
    elements: dict
    default: []
    suboptions:
      account:
        description: CLI account, as used by I(api_account).
        type: str
        required: true
      api_config:
        description: CLI configuration holding this account.
        type: str
      zones:
        description: Zones to query for this account. Defaults to I(zones).
        type: list
        elements: str
      filters:
        description: Filters for this account. Defaults to I(filters).
        type: dict
  inventory_hostname:
    description:
      - Value registered as inventory hostname.
      - With V(name), an instance whose name is used by another instance in any
        zone is keyed by its ID instead. With V(uuid), the ID is always used.
    type: str
    choices: ['name', 'uuid']
    default: name
  show_all:
    description:
      - List instances that have no address usable as C(ansible_host).
      - When V(false), those instances are dropped from the inventory.
    type: bool
    default: false
  use_names:
    description:
      - Use the instance name for C(ansible_host) and C(ansible_ssh_host).
      - Useful when instance names are FQDNs, for example on a bastion.
    type: bool
    default: false
  address_family:
    description:
      - Address family used to resolve C(ansible_host) from the instance.
    type: str
    choices: ['ipv4', 'ipv6']
    default: ipv4
  prefer_elastic_ip:
    description:
      - With I(address_family=ipv4), use the first attached Elastic IP instead of
        the public IP.
    type: bool
    default: true
  expand_hostvars:
    description:
      - Fetch each instance again with C(get-instance), which adds block storage
        volumes, snapshots, SSH keys, private networks and the cloud-init
        user-data to the C(exoscale) host variable.
      - Costs one additional API call per instance.
    type: bool
    default: false
  fail_on_errors:
    description:
      - Fail instead of warning when a zone cannot be fetched.
      - When V(false), the hosts of the remaining zones are returned.
    type: bool
    default: false
requirements:
  - python >= 3.11
  - exoscale >= 0.10.0
extends_documentation_fragment:
  - inventory_cache
  - constructed
  - poto.exoscale.exoscale
notes:
  - Every host carries the full instance description in the C(exoscale) host
    variable, its zone in C(exoscale_zone) and, when the account is known, that
    account in C(exoscale_account).
"""

EXAMPLES = r"""
# exoscale.yaml, then: ansible-inventory --list -i exoscale.yaml
plugin: poto.exoscale.exoscale

# credentials come from EXOSCALE_API_KEY and EXOSCALE_API_SECRET
zones:
  - ch-gva-2
  - de-fra-1

# credentials from the "production" account of the exo CLI configuration
---
plugin: poto.exoscale.exoscale
api_account: production
inventory_hostname: uuid
expand_hostvars: true
fail_on_errors: true
cache: true
cache_timeout: 3600
keyed_groups:
  - key: exoscale.labels.env
    prefix: env
  - key: exoscale_zone
    prefix: zone
compose:
  ansible_user: "'debian' if 'Debian' in exoscale.template.name else 'ubuntu'"

# two CLI accounts in one inventory, scoped independently
---
plugin: poto.exoscale.exoscale
accounts:
  - account: production
    zones: [ch-gva-2, de-fra-1]
  - account: staging
    zones: [ch-gva-2]
    filters:
      labels: "env=staging"
"""

import collections
import hashlib
import json
import subprocess
from typing import NamedTuple, cast

from ansible.errors import AnsibleError, AnsibleParserError
from ansible.inventory.data import InventoryData
from ansible.plugins.inventory import BaseInventoryPlugin, Cacheable, Constructable

from ..module_utils.exoscale import (
    DEFAULT_ZONE,
    FETCH_ERRORS,
    HAS_EXOSCALE,
    ExoscaleError,
    get_client,
    get_credentials,
)

# Filters accepted by list-instances, checked here to turn a typo into a config
# error instead of an SDK TypeError.
API_FILTERS = ("manager_id", "manager_type", "ip_address", "labels")


class _Scope(NamedTuple):
    """One account to read, with the zones and filters that apply to it."""

    account: str | None
    options: dict
    zones: list
    filters: dict


def _field(instance, name, default=None):
    """Read one instance attribute.

    The v2 API returns hyphenated keys (``public-ip``) while the SDK documents
    and accepts underscored ones, so both spellings are read.
    """
    return instance.get(name, instance.get(name.replace("_", "-"), default))


# ansible-core's own declarations disagree: Cacheable has `ansible_name: str`,
# the Constructable chain has `str | None`, so no class combining both matches.
class InventoryModule(  # pyright: ignore[reportIncompatibleVariableOverride]
    BaseInventoryPlugin, Constructable, Cacheable
):
    NAME = "poto.exoscale.exoscale"

    def verify_file(self, path):
        if super().verify_file(path):
            for suffix in ("yaml", "yml"):
                if path.endswith(f"exoscale.{suffix}"):
                    return True
        return False

    def parse(self, inventory, loader, path, cache=True):
        super().parse(inventory, loader, path, cache=cache)

        if not HAS_EXOSCALE:
            raise AnsibleParserError(
                "Could not import Python library exoscale, see"
                " https://github.com/exoscale/python-exoscale for installation"
                " instructions."
            )

        self._accept_short_name()
        self._read_config_data(path)

        instances = self._fetch_instances(path, cache)
        show_all = self.get_option("show_all")

        for hostname, instance in self._select_hostnames(instances).items():
            host_vars = self._host_vars(instance)
            if not show_all and "ansible_host" not in host_vars:
                continue
            self._add_host(hostname, host_vars)
            self._add_groups(hostname, instance)

    def _accept_short_name(self):
        # The loader fills _redirected_names from plugin routing only, so the
        # documented short form `plugin: exoscale` is accepted here.
        names = list(getattr(self, "_redirected_names", None) or [self.NAME])
        if "exoscale" not in names:
            self._redirected_names = [*names, "exoscale"]

    def _select_hostnames(self, instances):
        """Map inventory hostnames to instances, falling back to the ID."""
        if self.get_option("inventory_hostname") == "uuid":
            return {instance["id"]: instance for instance in instances}

        counts = collections.Counter(instance["name"] for instance in instances)
        return {
            instance["name"]
            if counts[instance["name"]] == 1
            else instance["id"]: instance
            for instance in instances
        }

    def _host_vars(self, instance):
        host_vars = {
            "exoscale": instance,
            "exoscale_zone": instance["zone"],
        }
        if instance.get("account"):
            host_vars["exoscale_account"] = instance["account"]

        if self.get_option("use_names"):
            address = instance["name"]
        else:
            address = self._address(instance)

        if address:
            host_vars["ansible_host"] = address
            host_vars["ansible_ssh_host"] = address

        return host_vars

    def _address(self, instance):
        """The instance address to reach it on, if it has one."""
        if self.get_option("address_family") == "ipv6":
            return _field(instance, "ipv6_address") or None

        if self.get_option("prefer_elastic_ip"):
            for elastic_ip in _field(instance, "elastic_ips") or []:
                if elastic_ip.get("ip"):
                    return elastic_ip["ip"]

        return _field(instance, "public_ip") or None

    def _add_host(self, hostname, host_vars):
        inventory = cast(InventoryData, self.inventory)  # set by parse()
        inventory.add_host(hostname, group="all")

        for name, value in host_vars.items():
            inventory.set_variable(hostname, name, value)

        strict = self.get_option("strict")

        self._set_composite_vars(
            self.get_option("compose"), host_vars, hostname, strict=True
        )
        self._add_host_to_composed_groups(
            self.get_option("groups"), host_vars, hostname, strict=strict
        )
        self._add_host_to_keyed_groups(
            self.get_option("keyed_groups"), host_vars, hostname, strict=strict
        )

    def _add_groups(self, hostname, instance):
        inventory = cast(InventoryData, self.inventory)  # set by parse()
        for group in self._groups(instance):
            group_name = inventory.add_group(group)
            if group_name == hostname:
                self.display.vvvv(
                    f"Same name for host {hostname} and group {group_name}"
                )
                inventory.add_host(hostname, group_name)
            else:
                inventory.add_child(group_name, hostname)

    def _groups(self, instance):
        """Groups generated from the instance attributes, before sanitisation."""
        zone = instance["zone"]
        groups = [instance["account"], zone] if instance.get("account") else [zone]

        state = _field(instance, "state")
        if state:
            groups += [state, f"{zone}_{state}"]

        for anti_affinity_group in _field(instance, "anti_affinity_groups") or []:
            if anti_affinity_group.get("name"):
                groups.append(anti_affinity_group["name"])

        manager = _field(instance, "manager") or {}
        if manager.get("type") == "instance-pool":
            groups.append("instance-pool")
            if manager.get("id"):
                groups.append(manager["id"])

        for key, value in (_field(instance, "labels") or {}).items():
            groups.append(f"label_{key}_{value}")

        groups.append(f"instance_{instance['id']}")
        return groups

    def _cache_key(self, path):
        """Cache prefix plus the fetch options, so a config edit misses the cache."""
        scope = {
            name: self.get_option(name)
            for name in (
                "api_account",
                "api_endpoint",
                "accounts",
                "expand_hostvars",
                "filters",
                "zones",
            )
        }
        fingerprint = hashlib.sha256(
            json.dumps(scope, sort_keys=True, default=str).encode()
        ).hexdigest()[:8]
        return f"{self._get_cache_prefix(path)}-{fingerprint}"

    def _fetch_instances(self, path, cache):
        cache_key = self._cache_key(path)
        user_cache_setting = self.get_option("cache")
        attempt_to_read_cache = user_cache_setting and cache
        cache_needs_update = not cache and user_cache_setting

        instances = None

        if attempt_to_read_cache:
            self.display.vvvv(f"Reading Exoscale inventory cache key {cache_key}")
            try:
                instances = self._cache[cache_key]
            except KeyError:
                self.display.vvvv("Exoscale inventory cache not found")
                cache_needs_update = True

        if not attempt_to_read_cache or cache_needs_update:
            instances = self._fetch_from_api()

        if cache_needs_update:
            self._cache[cache_key] = instances

        return instances

    def _fetch_from_api(self):
        try:
            return self._collect_instances()
        except ExoscaleError as error:
            raise AnsibleParserError(str(error)) from error

    @staticmethod
    def _account_name(entry):
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("account"), str)
            or not entry["account"]
        ):
            raise AnsibleParserError(
                f"Every accounts entry needs an account name, got {entry!r}."
            )

        return entry["account"]

    @staticmethod
    def _zones(value, context):
        """Zones as a list of names; suboptions get no conversion from Ansible."""
        if value is None:
            return []
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list) or not all(
            isinstance(zone, str) and zone for zone in value
        ):
            raise AnsibleParserError(f"zones{context} must be a list of zone names.")

        return value

    @staticmethod
    def _filters(value, context):
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise AnsibleParserError(f"filters{context} must be a dictionary.")

        unknown = sorted(set(value) - set(API_FILTERS))
        if unknown:
            raise AnsibleParserError(
                f"Unsupported filters{context}: {', '.join(unknown)}. Supported"
                f" filters are {', '.join(API_FILTERS)}."
            )

        return value

    def _scopes(self):
        """The accounts to read: one unnamed scope, or one per I(accounts) entry."""
        base = dict(self._options)
        accounts = self.get_option("accounts") or []

        if not accounts:
            return [
                _Scope(
                    account=None,
                    options=base,
                    zones=self._zones(self.get_option("zones"), ""),
                    filters=self._filters(self.get_option("filters"), ""),
                )
            ]

        # Ansible coerces a `type: list` option, so a bare string arrives as a
        # one-element list and is rejected by _account_name below.
        scopes = []
        for entry in accounts:
            account = self._account_name(entry)
            context = f" of account {account!r}"
            options = dict(base)
            options["api_account"] = account
            api_config = entry.get("api_config")
            if api_config:
                if not isinstance(api_config, str):
                    raise AnsibleParserError(
                        f"api_config of account {account!r} must be a string."
                    )
                options["api_config"] = api_config
            # an entry's own `zones: []` or `filters: {}` is a value, not a default
            zones = entry.get("zones")
            filters = entry.get("filters")
            scopes.append(
                _Scope(
                    account=account,
                    options=options,
                    zones=self._zones(
                        self.get_option("zones") if zones is None else zones, context
                    ),
                    filters=self._filters(
                        self.get_option("filters") if filters is None else filters,
                        context,
                    ),
                )
            )

        return scopes

    def _collect_instances(self):
        timeout = self.get_option("api_timeout")
        endpoint = self.get_option("api_endpoint")
        expand_hostvars = self.get_option("expand_hostvars")

        instances = []
        for scope in self._scopes():
            instances.extend(
                self._collect_scope(scope, endpoint, timeout, expand_hostvars)
            )

        return instances

    def _collect_scope(self, scope, endpoint, timeout, expand_hostvars):
        credentials = get_credentials(scope.options, run=self._run_secret_command)
        endpoint = endpoint or credentials.endpoint
        zones = scope.zones
        label = scope.account or credentials.account

        if endpoint and not zones:
            raise AnsibleParserError(
                "api_endpoint requires a non-empty zones list, zones cannot be"
                " discovered through an explicit endpoint."
            )

        if not zones:
            zones = self._discover_zones(credentials, timeout)

        instances = []
        for zone in zones:
            if endpoint:
                client = get_client(
                    credentials.key,
                    credentials.secret,
                    url=endpoint.replace("{zone}", zone),
                    timeout=timeout,
                )
            else:
                client = get_client(
                    credentials.key, credentials.secret, zone=zone, timeout=timeout
                )

            self.display.vvv(
                f"Fetching Exoscale instances from {label or 'account'} zone {zone}"
            )
            try:
                response = client.list_instances(**scope.filters) or {}
            except FETCH_ERRORS as error:
                self.display.warning(
                    f"Fetching instances from {label or 'account'} zone {zone}"
                    f" failed: {error}"
                )
                if self.get_option("fail_on_errors"):
                    raise
                continue

            for instance in response.get("instances") or []:
                instance["zone"] = zone
                if label:
                    instance["account"] = label
                if expand_hostvars:
                    instance = self._expand_instance(client, instance, zone, label)
                instances.append(instance)

        return instances

    @staticmethod
    def _run_secret_command(command):
        """Run an exo CLI `secretCommand`, returning what it printed."""
        try:
            result = subprocess.run(command, check=True, capture_output=True, text=True)
        except (OSError, subprocess.CalledProcessError) as error:
            raise ExoscaleError(
                f"The secretCommand {command[0]!r} failed: {error}"
            ) from error

        return result.stdout

    def _discover_zones(self, credentials, timeout):
        client = get_client(
            credentials.key, credentials.secret, zone=DEFAULT_ZONE, timeout=timeout
        )
        self.display.vvv(f"Listing Exoscale zones from {DEFAULT_ZONE}")
        try:
            response = client.list_zones() or {}
        except FETCH_ERRORS as error:
            raise AnsibleError(f"Listing the Exoscale zones failed: {error}") from error

        return [zone["name"] for zone in response.get("zones") or []]

    def _expand_instance(self, client, instance, zone, account):
        try:
            detail = client.get_instance(id=instance["id"])
        except FETCH_ERRORS as error:
            self.display.warning(f"Fetching instance {instance['id']} failed: {error}")
            if self.get_option("fail_on_errors"):
                raise
            return instance

        detail["zone"] = zone
        if account:
            detail["account"] = account
        return detail
