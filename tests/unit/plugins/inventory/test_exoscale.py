"""Unit tests for the poto.exoscale.exoscale inventory plugin."""

from __future__ import annotations

import copy

import pytest
import yaml
from ansible.errors import AnsibleError, AnsibleParserError
from ansible.inventory.data import InventoryData
from ansible.parsing.dataloader import DataLoader
from ansible.plugins.loader import inventory_loader
from ansible_collections.poto.exoscale.plugins.module_utils import (
    exoscale as module_utils,
)
from exoscale.api.exceptions import ExoscaleAPIClientException

PLUGIN_NAME = "poto.exoscale.exoscale"
ZONE = "ch-gva-2"
OTHER_ZONE = "de-fra-1"


def instance(**overrides):
    """An instance as list-instances returns it, hyphenated keys included."""
    data = {
        "id": "b1c6a3a4-0000-4000-8000-000000000001",
        "name": "web-1",
        "state": "running",
        "public-ip": "194.182.10.10",
        "ipv6-address": None,
        "elastic-ips": [],
        "security-groups": [{"id": "sg-1", "name": "web"}],
        "anti-affinity-groups": [],
        "instance-type": {"id": "it-1", "size": "medium", "family": "standard"},
        "template": {"id": "tpl-1", "name": "Linux Ubuntu 22.04 LTS 64-bit"},
        "labels": {},
        "manager": {},
    }
    data.update(overrides)
    return data


class FakeSession:
    """Stand-in for requests.Session, recording the arguments of each request."""

    def __init__(self):
        self.requests = []

    def request(self, **kwargs):
        self.requests.append(kwargs)


class FakeClient:
    """Stand-in for exoscale.api.v2.Client, recording calls instead of dialing out."""

    def __init__(self, api, zone=None):
        self.api = api
        self.zone = zone
        self.http_client = FakeSession()

    def list_zones(self):
        self.api.calls.append(("list_zones",))
        return {"zones": [{"name": zone} for zone in self.api.zones]}

    def list_instances(self, **filters):
        self.api.calls.append(("list_instances", self.zone, filters))
        if self.zone in self.api.failing_zones:
            raise ExoscaleAPIClientException(f"zone {self.zone} is unavailable")
        return {"instances": copy.deepcopy(self.api.instances.get(self.zone, []))}

    def get_instance(self, id):
        self.api.calls.append(("get_instance", id))
        return copy.deepcopy(self.api.details[id])


class FakeAPI:
    def __init__(self):
        self.zones = [ZONE]
        self.instances = {}
        self.details = {}
        self.failing_zones = set()
        self.calls = []
        self.clients = []

    def client(self, key, secret, zone=None, url=None, timeout=None):
        self.calls.append(("client", zone, url))
        client = FakeClient(self, zone=zone)
        self.clients.append(client)
        return client

    def ops(self):
        return [call[0] for call in self.calls]


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for name in (
        "EXOSCALE_API_KEY",
        "EXOSCALE_API_SECRET",
        "EXOSCALE_CONFIG",
        "EXOSCALE_PROFILE",
    ):
        monkeypatch.delenv(name, raising=False)
    # keep the default search path out of the picture
    monkeypatch.setattr(module_utils, "CONFIG_FILES", ())


@pytest.fixture
def api(monkeypatch):
    fake = FakeAPI()
    monkeypatch.setattr(module_utils, "_client_class", lambda: fake.client)
    return fake


@pytest.fixture
def credentials(monkeypatch):
    monkeypatch.setenv("EXOSCALE_API_KEY", "key")
    monkeypatch.setenv("EXOSCALE_API_SECRET", "secret")


@pytest.fixture
def config(tmp_path):
    def write(**options):
        options.setdefault("plugin", PLUGIN_NAME)
        path = tmp_path / "exoscale.yaml"
        path.write_text(yaml.safe_dump(options))
        return path

    return write


@pytest.fixture
def parse(credentials):
    def run(path, cache=True):
        plugin = inventory_loader.get(PLUGIN_NAME)
        inventory = InventoryData()
        plugin.parse(inventory, DataLoader(), str(path), cache=cache)
        try:
            plugin.update_cache_if_changed()
        except AttributeError:
            pass
        return inventory

    return run


def hosts(inventory):
    return sorted(inventory.hosts)


def host_vars(inventory, hostname):
    return inventory.get_host(hostname).get_vars()


def groups_of(inventory, hostname):
    return {group.name for group in inventory.get_host(hostname).get_groups()}


@pytest.mark.parametrize(
    ("name", "accepted"),
    [
        ("exoscale.yaml", True),
        ("exoscale.yml", True),
        ("other.yaml", False),
        ("exoscale.json", False),
    ],
)
def test_verify_file(tmp_path, name, accepted):
    path = tmp_path / name
    path.write_text("")

    plugin = inventory_loader.get(PLUGIN_NAME)

    assert plugin.verify_file(str(path)) is accepted


def test_verify_file_rejects_missing_file(tmp_path):
    plugin = inventory_loader.get(PLUGIN_NAME)

    assert plugin.verify_file(str(tmp_path / "exoscale.yaml")) is False


def test_short_plugin_name_is_accepted(api, parse, config):
    api.instances[ZONE] = [instance()]

    inventory = parse(config(plugin="exoscale"))

    assert hosts(inventory) == ["web-1"]


def test_credentials_option_beats_environment_and_ini(tmp_path, monkeypatch):
    monkeypatch.setenv("EXOSCALE_API_KEY", "env-key")
    monkeypatch.setenv("EXOSCALE_API_SECRET", "env-secret")
    ini = tmp_path / "exoscale.ini"
    ini.write_text("[default]\nkey = ini-key\nsecret = ini-secret\n")

    credentials = module_utils.get_credentials(
        {
            "api_key": "option-key",
            "api_secret": "option-secret",
            "api_config": str(ini),
        }
    )

    assert credentials == ("option-key", "option-secret")


def test_credentials_fall_back_to_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("EXOSCALE_API_KEY", "env-key")
    monkeypatch.setenv("EXOSCALE_API_SECRET", "env-secret")
    ini = tmp_path / "exoscale.ini"
    ini.write_text("[default]\nkey = ini-key\nsecret = ini-secret\n")

    credentials = module_utils.get_credentials({"api_config": str(ini)})

    assert credentials == ("env-key", "env-secret")


def test_credentials_fall_back_to_ini_per_value(tmp_path, monkeypatch):
    monkeypatch.setenv("EXOSCALE_API_KEY", "env-key")
    ini = tmp_path / "exoscale.ini"
    ini.write_text("[default]\nkey = ini-key\nsecret = ini-secret\n")

    credentials = module_utils.get_credentials({"api_config": str(ini)})

    assert credentials == ("env-key", "ini-secret")


def test_credentials_profile_selects_the_ini_section(tmp_path):
    ini = tmp_path / "exoscale.ini"
    ini.write_text(
        "[default]\nkey = default-key\nsecret = default-secret\n"
        "\n[production]\nkey = prod-key\nsecret = prod-secret\n"
    )

    credentials = module_utils.get_credentials(
        {"api_config": str(ini), "api_profile": "production"}
    )

    assert credentials == ("prod-key", "prod-secret")


def test_credentials_config_file_is_required_when_given(tmp_path):
    with pytest.raises(module_utils.ExoscaleError, match="does not exist"):
        module_utils.get_credentials({"api_config": str(tmp_path / "nope.ini")})


def test_missing_credentials_raise(config, api):
    # no credentials fixture here: this test needs the environment empty
    plugin = inventory_loader.get(PLUGIN_NAME)

    with pytest.raises(AnsibleParserError, match="Missing Exoscale credentials"):
        plugin.parse(InventoryData(), DataLoader(), str(config()), cache=False)

    assert api.calls == []


def test_zone_discovery(api, parse, config):
    api.zones = [ZONE, OTHER_ZONE]
    api.instances[ZONE] = [instance()]
    api.instances[OTHER_ZONE] = [instance(id="other-id", name="web-2")]

    inventory = parse(config())

    assert hosts(inventory) == ["web-1", "web-2"]
    assert "list_zones" in api.ops()


def test_explicit_zones_skip_zone_discovery(api, parse, config):
    api.zones = [ZONE, OTHER_ZONE]
    api.instances[OTHER_ZONE] = [instance(name="web-2")]

    inventory = parse(config(zones=[OTHER_ZONE]))

    assert hosts(inventory) == ["web-2"]
    assert "list_zones" not in api.ops()


def test_duplicate_names_across_zones_are_keyed_by_id(api, parse, config):
    first = instance()
    second = instance(id="c2d7b4b5-0000-4000-8000-000000000002")
    api.zones = [ZONE, OTHER_ZONE]
    api.instances[ZONE] = [first]
    api.instances[OTHER_ZONE] = [second]

    inventory = parse(config())

    assert hosts(inventory) == [first["id"], second["id"]]


def test_inventory_hostname_uuid(api, parse, config):
    api.instances[ZONE] = [instance()]

    inventory = parse(config(inventory_hostname="uuid"))

    assert hosts(inventory) == ["b1c6a3a4-0000-4000-8000-000000000001"]


def test_instance_without_address_is_dropped(api, parse, config):
    api.instances[ZONE] = [instance(**{"public-ip": None})]

    inventory = parse(config())

    assert hosts(inventory) == []


def test_show_all_keeps_instance_without_address(api, parse, config):
    api.instances[ZONE] = [instance(**{"public-ip": None})]

    inventory = parse(config(show_all=True))

    assert hosts(inventory) == ["web-1"]
    assert "ansible_host" not in host_vars(inventory, "web-1")


def test_elastic_ip_is_preferred(api, parse, config):
    api.instances[ZONE] = [
        instance(**{"elastic-ips": [{"id": "eip-1", "ip": "5.5.5.5"}]})
    ]

    inventory = parse(config())

    assert host_vars(inventory, "web-1")["ansible_host"] == "5.5.5.5"


def test_prefer_elastic_ip_can_be_disabled(api, parse, config):
    api.instances[ZONE] = [
        instance(**{"elastic-ips": [{"id": "eip-1", "ip": "5.5.5.5"}]})
    ]

    inventory = parse(config(prefer_elastic_ip=False))

    assert host_vars(inventory, "web-1")["ansible_host"] == "194.182.10.10"


def test_ipv6_address_family(api, parse, config):
    api.instances[ZONE] = [instance(**{"ipv6-address": "2001:db8::1"})]

    inventory = parse(config(address_family="ipv6"))

    assert host_vars(inventory, "web-1")["ansible_host"] == "2001:db8::1"


def test_use_names_sets_the_instance_name(api, parse, config):
    api.instances[ZONE] = [instance()]

    inventory = parse(config(use_names=True))

    host_vars_of_host = host_vars(inventory, "web-1")
    assert host_vars_of_host["ansible_host"] == "web-1"
    assert host_vars_of_host["ansible_ssh_host"] == "web-1"


def test_both_address_variables_are_set(api, parse, config):
    api.instances[ZONE] = [instance()]

    inventory = parse(config())

    host_vars_of_host = host_vars(inventory, "web-1")
    assert host_vars_of_host["ansible_host"] == "194.182.10.10"
    assert host_vars_of_host["ansible_ssh_host"] == "194.182.10.10"


def test_hostvars_expose_the_instance(api, parse, config):
    api.instances[ZONE] = [instance()]

    inventory = parse(config())

    host_vars_of_host = host_vars(inventory, "web-1")
    assert host_vars_of_host["exoscale_zone"] == ZONE
    assert host_vars_of_host["exoscale"]["zone"] == ZONE
    assert host_vars_of_host["exoscale"]["id"] == "b1c6a3a4-0000-4000-8000-000000000001"


def test_automatic_groups(api, parse, config):
    api.instances[ZONE] = [
        instance(
            **{
                "anti-affinity-groups": [{"id": "aag-1", "name": "db-spread"}],
                "labels": {"env": "dev"},
                "manager": {"id": "pool-1", "type": "instance-pool"},
            }
        )
    ]

    inventory = parse(config())

    assert groups_of(inventory, "web-1") >= {
        ZONE,
        "running",
        "medium",
        "standard",
        "Linux Ubuntu 22.04 LTS 64-bit",
        "web",
        "db-spread",
        "instance-pool",
        "pool-1",
        "label_env_dev",
        "instance_b1c6a3a4-0000-4000-8000-000000000001",
    }


def test_zone_qualified_groups(api, parse, config):
    api.instances[ZONE] = [instance()]

    inventory = parse(config())

    assert groups_of(inventory, "web-1") >= {
        f"{ZONE}_running",
        f"{ZONE}_Linux Ubuntu 22.04 LTS 64-bit",
    }


def test_manager_outside_an_instance_pool_adds_no_pool_group(api, parse, config):
    api.instances[ZONE] = [instance(manager={"id": "x", "type": ""})]

    inventory = parse(config())

    assert "instance-pool" not in groups_of(inventory, "web-1")


def test_failing_zone_warns_and_keeps_the_other_zones(api, parse, config):
    api.zones = [ZONE, OTHER_ZONE]
    api.failing_zones = {OTHER_ZONE}
    api.instances[ZONE] = [instance()]

    inventory = parse(config())

    assert hosts(inventory) == ["web-1"]


def test_fail_on_errors_propagates(api, parse, config):
    api.zones = [ZONE, OTHER_ZONE]
    api.failing_zones = {OTHER_ZONE}

    with pytest.raises(ExoscaleAPIClientException):
        parse(config(fail_on_errors=True))


def test_warm_cache_makes_no_api_calls(api, parse, config, tmp_path):
    api.instances[ZONE] = [instance()]
    path = config(
        cache=True,
        cache_plugin="jsonfile",
        cache_connection=str(tmp_path / "cache"),
    )

    first = parse(path)
    calls = len(api.calls)
    second = parse(path)

    assert hosts(first) == hosts(second) == ["web-1"]
    assert len(api.calls) == calls


def test_constructs_groups_and_vars_from_the_exoscale_hostvar(api, parse, config):
    api.instances[ZONE] = [instance(labels={"env": "dev"})]

    inventory = parse(
        config(
            keyed_groups=[
                {"key": "exoscale.labels.env", "prefix": "env"},
                {"key": "exoscale_zone", "prefix": "zone"},
            ],
            compose={
                "ansible_user": (
                    "'debian' if 'Debian' in exoscale.template.name else 'ubuntu'"
                )
            },
        )
    )

    # keyed groups are sanitized by Ansible, unlike the automatic ones
    assert groups_of(inventory, "web-1") >= {"env_dev", "zone_ch_gva_2"}
    assert host_vars(inventory, "web-1")["ansible_user"] == "ubuntu"


def test_expand_hostvars_fetches_instance_details(api, parse, config):
    api.instances[ZONE] = [instance()]
    api.details["b1c6a3a4-0000-4000-8000-000000000001"] = instance(
        **{"private-networks": [{"id": "net-1"}]}
    )

    inventory = parse(config(expand_hostvars=True))

    assert host_vars(inventory, "web-1")["exoscale"]["private-networks"] == [
        {"id": "net-1"}
    ]
    assert "get_instance" in api.ops()


def test_api_endpoint_is_formatted_per_zone(api, parse, config):
    api.instances[ZONE] = [instance()]

    parse(config(zones=[ZONE], api_endpoint="https://proxy.example/{zone}/v2"))

    assert ("client", None, f"https://proxy.example/{ZONE}/v2") in api.calls


def test_api_endpoint_requires_zones(api, parse, config):
    with pytest.raises(AnsibleParserError, match="api_endpoint requires"):
        parse(config(api_endpoint="https://proxy.example/v2"))

    assert api.calls == []


def test_filters_are_forwarded(api, parse, config):
    parse(config(filters={"labels": "env=dev", "manager_type": "instance-pool"}))

    assert (
        "list_instances",
        ZONE,
        {"labels": "env=dev", "manager_type": "instance-pool"},
    ) in (api.calls)


def test_unknown_filter_raises(api, parse, config):
    with pytest.raises(AnsibleParserError, match="Unsupported filters: nonsense"):
        parse(config(filters={"nonsense": "x"}))


def test_zone_discovery_failure_is_fatal(api, parse, config, monkeypatch):
    api.zones = [ZONE]

    def explode():
        raise ExoscaleAPIClientException("unreachable")

    monkeypatch.setattr(FakeClient, "list_zones", lambda self: explode())

    with pytest.raises(AnsibleError, match="Listing the Exoscale zones failed"):
        parse(config())


def test_timeout_is_bound_on_the_session(api, parse, config):
    parse(config(zones=[ZONE], api_timeout=5))

    api.clients[0].http_client.request(method="get", url="https://api.example")

    assert api.clients[0].http_client.requests == [
        {"method": "get", "url": "https://api.example", "timeout": 5}
    ]


def test_timeout_defaults_to_30_seconds(api, parse, config):
    parse(config(zones=[ZONE]))

    api.clients[0].http_client.request(method="get", url="https://api.example")

    assert api.clients[0].http_client.requests[0]["timeout"] == 30
