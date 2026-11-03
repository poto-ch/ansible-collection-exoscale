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
      - Fetch each instance again with C(get-instance), which adds private
        networks and snapshots to the C(exoscale) host variable.
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
  - python >= 3.9
  - exoscale >= 0.10.0
extends_documentation_fragment:
  - inventory_cache
  - constructed
  - poto.exoscale.exoscale
notes:
  - Every host carries the full instance description in the C(exoscale) host
    variable and its zone in C(exoscale_zone).
"""

EXAMPLES = r"""
# exoscale.yaml, then: ansible-inventory --list -i exoscale.yaml
plugin: poto.exoscale.exoscale

# credentials come from EXOSCALE_API_KEY and EXOSCALE_API_SECRET
zones:
  - ch-gva-2
  - de-fra-1

# credentials from the [production] section of ~/.exoscale.ini
---
plugin: poto.exoscale.exoscale
api_profile: production
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
"""

import collections
from typing import cast

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


def _field(instance, name, default=None):
    """Read one instance attribute.

    The v2 API returns hyphenated keys (``public-ip``) while the SDK documents
    and accepts underscored ones, so both spellings are read.
    """
    return instance.get(name, instance.get(name.replace("_", "-"), default))


# pyright: ansible-core declares ansible_name as str (Cacheable) and str | None
# (Constructable), which no class combining both can satisfy.
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
        groups = [zone]

        state = _field(instance, "state")
        if state:
            groups += [state, f"{zone}_{state}"]

        instance_type = _field(instance, "instance_type") or {}
        for attribute in ("size", "family"):
            if instance_type.get(attribute):
                groups.append(instance_type[attribute])

        template = _field(instance, "template") or {}
        if template.get("name"):
            groups += [template["name"], f"{zone}_{template['name']}"]

        for security_group in _field(instance, "security_groups") or []:
            if security_group.get("name"):
                groups.append(security_group["name"])

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

    def _fetch_instances(self, path, cache):
        cache_key = self._get_cache_prefix(path)
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

    def _collect_instances(self):
        options = dict(self._options)
        key, secret = get_credentials(options)
        timeout = self.get_option("api_timeout")
        endpoint = self.get_option("api_endpoint")
        zones = self.get_option("zones") or []
        filters = self.get_option("filters") or {}
        expand_hostvars = self.get_option("expand_hostvars")

        unknown_filters = sorted(set(filters) - set(API_FILTERS))
        if unknown_filters:
            raise AnsibleParserError(
                f"Unsupported filters: {', '.join(unknown_filters)}. Supported"
                f" filters are {', '.join(API_FILTERS)}."
            )

        if endpoint and not zones:
            raise AnsibleParserError(
                "api_endpoint requires a non-empty zones list, zones cannot be"
                " discovered through an explicit endpoint."
            )

        if not zones:
            zones = self._discover_zones(key, secret, timeout)

        instances = []
        for zone in zones:
            if endpoint:
                client = get_client(
                    key,
                    secret,
                    url=endpoint.replace("{zone}", zone),
                    timeout=timeout,
                )
            else:
                client = get_client(key, secret, zone=zone, timeout=timeout)

            self.display.vvv(f"Fetching Exoscale instances from zone {zone}")
            try:
                response = client.list_instances(**filters) or {}
            except FETCH_ERRORS as error:
                self.display.warning(
                    f"Fetching instances from zone {zone} failed: {error}"
                )
                if self.get_option("fail_on_errors"):
                    raise
                continue

            for instance in response.get("instances") or []:
                instance["zone"] = zone
                if expand_hostvars:
                    instance = self._expand_instance(client, instance, zone)
                instances.append(instance)

        return instances

    def _discover_zones(self, key, secret, timeout):
        client = get_client(key, secret, zone=DEFAULT_ZONE, timeout=timeout)
        self.display.vvv(f"Listing Exoscale zones from {DEFAULT_ZONE}")
        try:
            response = client.list_zones() or {}
        except FETCH_ERRORS as error:
            raise AnsibleError(f"Listing the Exoscale zones failed: {error}") from error

        return [zone["name"] for zone in response.get("zones") or []]

    def _expand_instance(self, client, instance, zone):
        try:
            detail = client.get_instance(id=instance["id"])
        except FETCH_ERRORS as error:
            self.display.warning(f"Fetching instance {instance['id']} failed: {error}")
            if self.get_option("fail_on_errors"):
                raise
            return instance

        detail["zone"] = zone
        return detail
