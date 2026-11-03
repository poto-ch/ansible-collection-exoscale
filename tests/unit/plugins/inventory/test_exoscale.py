"""Unit tests for the poto.exoscale.exoscale inventory plugin."""

from __future__ import annotations

import copy
import json

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

ACCOUNT = {"name": "default", "key": "account-key", "secret": "account-secret"}


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

    def __init__(self, api, key, zone=None):
        self.api = api
        self.key = key
        self.zone = zone
        self.http_client = FakeSession()

    def list_zones(self):
        self.api.calls.append(("list_zones",))
        return {"zones": [{"name": zone} for zone in self.api.zones]}

    def list_instances(self, **filters):
        self.api.calls.append(("list_instances", self.zone, filters))
        if self.zone in self.api.failing_zones:
            raise ExoscaleAPIClientException(f"zone {self.zone} is unavailable")
        per_account = self.api.account_instances.get((self.key, self.zone))
        if per_account is None:
            per_account = self.api.instances.get(self.zone, [])
        return {"instances": copy.deepcopy(per_account)}

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
        self.credentials = []
        self.account_instances = {}

    def client(self, key, secret, zone=None, url=None, timeout=None):
        self.calls.append(("client", zone, url))
        self.credentials.append((key, secret))
        client = FakeClient(self, key, zone=zone)
        self.clients.append(client)
        return client

    def ops(self):
        return [call[0] for call in self.calls]


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for name in (
        "EXOSCALE_API_KEY",
        "EXOSCALE_API_SECRET",
        "EXOSCALE_ACCOUNT",
        "EXOSCALE_CONFIG",
        "EXOSCALE_INVENTORY_TIMEOUT",
        # the CLI reads this one as a duration, the plugin must ignore it
        "EXOSCALE_TIMEOUT",
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


def cli_config(tmp_path, *, defaultaccount=None, accounts=(), filename="exoscale.toml"):
    """Write an exo CLI configuration file."""
    lines = []
    if defaultaccount:
        lines.append(f'defaultaccount = "{defaultaccount}"')
    for account in accounts:
        lines.append("")
        lines.append("[[accounts]]")
        for name, value in account.items():
            if isinstance(value, list):
                lines.append(f"{name} = {json.dumps(value)}")
            else:
                lines.append(f'{name} = "{value}"')
    path = tmp_path / filename
    path.write_text("\n".join(lines) + "\n")
    return path


PRODUCTION = {"name": "production", "key": "prod-key", "secret": "prod-secret"}
STAGING = {"name": "staging", "key": "staging-key", "secret": "staging-secret"}


def two_account_config(tmp_path):
    return cli_config(
        tmp_path, defaultaccount="production", accounts=[PRODUCTION, STAGING]
    )


def test_credentials_option_beats_environment_and_config(tmp_path, monkeypatch):
    monkeypatch.setenv("EXOSCALE_API_KEY", "env-key")
    monkeypatch.setenv("EXOSCALE_API_SECRET", "env-secret")
    path = cli_config(tmp_path, defaultaccount="default", accounts=[ACCOUNT])

    credentials = module_utils.get_credentials(
        {
            "api_key": "option-key",
            "api_secret": "option-secret",
            "api_config": str(path),
        }
    )

    assert (credentials.key, credentials.secret) == ("option-key", "option-secret")
    assert credentials.account is None


def test_credentials_fall_back_to_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("EXOSCALE_API_KEY", "env-key")
    monkeypatch.setenv("EXOSCALE_API_SECRET", "env-secret")
    path = cli_config(tmp_path, defaultaccount="default", accounts=[ACCOUNT])

    credentials = module_utils.get_credentials({"api_config": str(path)})

    assert (credentials.key, credentials.secret) == ("env-key", "env-secret")


def test_credentials_fall_back_to_config_per_value(tmp_path, monkeypatch):
    monkeypatch.setenv("EXOSCALE_API_KEY", "env-key")
    path = cli_config(tmp_path, defaultaccount="default", accounts=[ACCOUNT])

    credentials = module_utils.get_credentials({"api_config": str(path)})

    assert (credentials.key, credentials.secret) == ("env-key", "account-secret")


def test_credentials_report_the_cli_account(tmp_path):
    path = cli_config(tmp_path, defaultaccount="default", accounts=[ACCOUNT])

    credentials = module_utils.get_credentials({"api_config": str(path)})

    assert credentials.account == "default"


def test_defaultaccount_selects_the_account(tmp_path):
    path = cli_config(
        tmp_path,
        defaultaccount="production",
        accounts=[ACCOUNT, dict(ACCOUNT, name="production", key="prod-key")],
    )

    credentials = module_utils.get_credentials({"api_config": str(path)})

    assert credentials.key == "prod-key"


def test_api_account_overrides_defaultaccount(tmp_path):
    path = cli_config(
        tmp_path,
        defaultaccount="production",
        accounts=[ACCOUNT, dict(ACCOUNT, name="production", key="prod-key")],
    )

    credentials = module_utils.get_credentials(
        {"api_config": str(path), "api_account": "default"}
    )

    assert credentials.key == "account-key"


def test_environment_account_overrides_defaultaccount(tmp_path, monkeypatch):
    monkeypatch.setenv("EXOSCALE_ACCOUNT", "default")
    path = cli_config(
        tmp_path,
        defaultaccount="production",
        accounts=[ACCOUNT, dict(ACCOUNT, name="production", key="prod-key")],
    )

    credentials = module_utils.get_credentials({"api_config": str(path)})

    assert credentials.key == "account-key"


def test_single_account_without_defaultaccount(tmp_path):
    path = cli_config(tmp_path, accounts=[ACCOUNT])

    credentials = module_utils.get_credentials({"api_config": str(path)})

    assert credentials.key == "account-key"


def test_unknown_account_raises(tmp_path):
    path = cli_config(tmp_path, defaultaccount="default", accounts=[ACCOUNT])

    with pytest.raises(module_utils.ExoscaleError, match="'nope' does not exist"):
        module_utils.get_credentials({"api_config": str(path), "api_account": "nope"})


def test_config_requires_an_account_when_ambiguous(tmp_path):
    path = cli_config(tmp_path, accounts=[ACCOUNT, dict(ACCOUNT, name="production")])

    with pytest.raises(module_utils.ExoscaleError, match="Set api_account"):
        module_utils.get_credentials({"api_config": str(path)})


def test_credentials_config_file_is_required_when_given(tmp_path):
    with pytest.raises(module_utils.ExoscaleError, match="does not exist"):
        module_utils.get_credentials({"api_config": str(tmp_path / "nope.toml")})


def test_credentials_config_is_required_when_environment_is_empty():
    with pytest.raises(
        module_utils.ExoscaleError, match="No Exoscale CLI configuration"
    ):
        module_utils.get_credentials({})


def secret_command_config(tmp_path, command):
    return cli_config(
        tmp_path,
        defaultaccount="default",
        accounts=[{"name": "default", "key": "account-key", "secretCommand": command}],
    )


def test_secret_command_uses_the_runner(tmp_path):
    path = secret_command_config(tmp_path, ["pass", "show", "exoscale"])
    commands = []

    credentials = module_utils.get_credentials(
        {"api_config": str(path)},
        run=lambda command: commands.append(command) or "command-secret\n",
    )

    assert credentials.secret == "command-secret"
    assert commands == [["pass", "show", "exoscale"]]


def test_secret_command_without_a_runner_raises(tmp_path):
    path = secret_command_config(tmp_path, ["pass", "show", "exoscale"])

    with pytest.raises(module_utils.ExoscaleError, match="cannot run"):
        module_utils.get_credentials({"api_config": str(path)})


def test_plugin_runs_the_secret_command(api, config, tmp_path, monkeypatch):
    cli = secret_command_config(tmp_path, ["sh", "-c", "echo command-secret"])

    path = config(api_config=str(cli), zones=[ZONE])

    plugin = inventory_loader.get(PLUGIN_NAME)
    plugin.parse(InventoryData(), DataLoader(), str(path), cache=False)

    assert api.credentials == [("account-key", "command-secret")]


def test_plugin_reports_a_failing_secret_command(api, config, tmp_path):
    cli = secret_command_config(tmp_path, ["false"])

    plugin = inventory_loader.get(PLUGIN_NAME)
    with pytest.raises(AnsibleParserError, match="secretCommand"):
        plugin.parse(
            InventoryData(),
            DataLoader(),
            str(config(api_config=str(cli))),
            cache=False,
        )


def test_secret_command_must_be_a_list(tmp_path):
    path = cli_config(
        tmp_path,
        defaultaccount="default",
        accounts=[
            {"name": "default", "key": "account-key", "secretCommand": "pass show"}
        ],
    )

    with pytest.raises(module_utils.ExoscaleError, match="must be a list"):
        module_utils.get_credentials({"api_config": str(path)})


def test_config_without_accounts_says_so(tmp_path):
    path = tmp_path / "exoscale.toml"
    path.write_text("")

    with pytest.raises(module_utils.ExoscaleError, match="has no accounts"):
        module_utils.get_credentials({"api_config": str(path)})


def test_accounts_must_be_an_array_of_tables(tmp_path):
    path = tmp_path / "exoscale.toml"
    path.write_text('defaultaccount = "default"\naccounts = "default"\n')

    with pytest.raises(module_utils.ExoscaleError, match="array of tables"):
        module_utils.get_credentials({"api_config": str(path)})


def test_account_endpoint_is_returned(tmp_path):
    path = cli_config(
        tmp_path,
        defaultaccount="default",
        accounts=[dict(ACCOUNT, endpoint="https://proxy.example/v2")],
    )

    credentials = module_utils.get_credentials({"api_config": str(path)})

    assert credentials.endpoint == "https://proxy.example/v2"


def test_missing_credentials_raise(config, api):
    # no credentials fixture here: this test needs the environment empty
    plugin = inventory_loader.get(PLUGIN_NAME)

    with pytest.raises(AnsibleParserError, match="No Exoscale CLI configuration"):
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


def test_two_accounts_in_one_inventory(api, parse, config, tmp_path, monkeypatch):
    monkeypatch.delenv("EXOSCALE_API_KEY")
    monkeypatch.delenv("EXOSCALE_API_SECRET")
    cli = two_account_config(tmp_path)
    api.account_instances[("prod-key", ZONE)] = [instance()]
    api.account_instances[("staging-key", ZONE)] = [
        instance(id="staging-id", name="web-2")
    ]

    inventory = parse(
        config(
            accounts=[{"account": "production"}, {"account": "staging"}],
            zones=[ZONE],
            api_config=str(cli),
        )
    )

    assert hosts(inventory) == ["web-1", "web-2"]
    assert host_vars(inventory, "web-1")["exoscale_account"] == "production"
    assert host_vars(inventory, "web-2")["exoscale_account"] == "staging"
    assert "production" in groups_of(inventory, "web-1")
    assert "staging" in groups_of(inventory, "web-2")
    assert {entry[0] for entry in api.credentials} == {"prod-key", "staging-key"}


def test_duplicate_names_across_accounts_are_keyed_by_id(
    api, parse, config, tmp_path, monkeypatch
):
    monkeypatch.delenv("EXOSCALE_API_KEY")
    monkeypatch.delenv("EXOSCALE_API_SECRET")
    cli = two_account_config(tmp_path)
    api.account_instances[("prod-key", ZONE)] = [instance()]
    api.account_instances[("staging-key", ZONE)] = [instance(id="staging-id")]

    inventory = parse(
        config(
            accounts=[{"account": "production"}, {"account": "staging"}],
            zones=[ZONE],
            api_config=str(cli),
        )
    )

    assert hosts(inventory) == [instance()["id"], "staging-id"]


def test_account_filters_and_zones_override_the_top_level(
    api, parse, config, tmp_path, monkeypatch
):
    monkeypatch.delenv("EXOSCALE_API_KEY")
    monkeypatch.delenv("EXOSCALE_API_SECRET")
    cli = two_account_config(tmp_path)

    parse(
        config(
            accounts=[
                {
                    "account": "staging",
                    "zones": [OTHER_ZONE],
                    "filters": {"labels": "env=staging"},
                }
            ],
            zones=[ZONE],
            filters={"labels": "env=production"},
            api_config=str(cli),
        )
    )

    assert ("list_instances", OTHER_ZONE, {"labels": "env=staging"}) in api.calls
    assert ("list_instances", ZONE, {"labels": "env=production"}) not in api.calls


@pytest.mark.parametrize("entry", [{"zones": [ZONE]}, "production", {"account": ""}])
def test_account_entry_needs_a_name(api, parse, config, entry):
    with pytest.raises(AnsibleParserError, match="needs an account name"):
        parse(config(accounts=[entry]))

    assert api.calls == []


def test_account_zones_as_a_string(api, parse, config):
    parse(config(accounts=[{"account": "default", "zones": OTHER_ZONE}]))

    assert ("list_instances", OTHER_ZONE, {}) in api.calls


def test_account_filters_must_be_a_dictionary(api, parse, config):
    with pytest.raises(
        AnsibleParserError, match="filters of account 'default' must be a dictionary"
    ):
        parse(config(accounts=[{"account": "default", "filters": "labels=dev"}]))

    assert api.calls == []


def test_account_api_config_must_be_a_string(api, parse, config):
    with pytest.raises(AnsibleParserError, match="api_config of account"):
        parse(config(accounts=[{"account": "default", "api_config": ["x"]}]))

    assert api.calls == []


def test_account_api_config_selects_another_file(
    api, parse, config, tmp_path, monkeypatch
):
    monkeypatch.delenv("EXOSCALE_API_KEY")
    monkeypatch.delenv("EXOSCALE_API_SECRET")
    default = two_account_config(tmp_path)
    other = cli_config(
        tmp_path,
        filename="other.toml",
        defaultaccount="staging",
        accounts=[dict(STAGING, key="other-key")],
    )
    api.account_instances[("other-key", ZONE)] = [instance(name="web-other")]

    inventory = parse(
        config(
            accounts=[{"account": "staging", "api_config": str(other)}],
            zones=[ZONE],
            api_config=str(default),
        )
    )

    assert hosts(inventory) == ["web-other"]
    assert api.credentials == [("other-key", "staging-secret")]


def test_unknown_filter_in_an_account_raises(api, parse, config, tmp_path):
    with pytest.raises(
        AnsibleParserError, match="Unsupported filters of account 'production'"
    ):
        parse(config(accounts=[{"account": "production", "filters": {"nonsense": 1}}]))

    assert api.calls == []


def test_single_account_is_labelled_with_its_cli_account(
    api, parse, config, tmp_path, monkeypatch
):
    monkeypatch.delenv("EXOSCALE_API_KEY")
    monkeypatch.delenv("EXOSCALE_API_SECRET")
    cli = cli_config(tmp_path, defaultaccount="default", accounts=[ACCOUNT])
    api.instances[ZONE] = [instance()]

    inventory = parse(config(api_config=str(cli)))

    assert host_vars(inventory, "web-1")["exoscale_account"] == "default"
    assert "default" in groups_of(inventory, "web-1")


def test_cache_invalidates_when_the_zones_change(api, parse, config, tmp_path):
    cache = {
        "cache": True,
        "cache_plugin": "jsonfile",
        "cache_connection": str(tmp_path / "cache"),
    }

    parse(config(zones=[ZONE], **cache))
    calls = len(api.calls)
    parse(config(zones=[ZONE], **cache))
    assert len(api.calls) == calls

    parse(config(zones=[ZONE, OTHER_ZONE], **cache))
    assert len(api.calls) > calls


def test_entry_zones_empty_means_discovery(api, parse, config):
    parse(config(accounts=[{"account": "default", "zones": []}], zones=[OTHER_ZONE]))

    assert "list_zones" in api.ops()
    assert ("list_instances", ZONE, {}) in api.calls
    assert ("list_instances", OTHER_ZONE, {}) not in api.calls


def test_entry_filters_empty_does_not_inherit(api, parse, config):
    parse(
        config(
            accounts=[{"account": "default", "filters": {}}],
            filters={"labels": "env=production"},
        )
    )

    assert ("list_instances", ZONE, {}) in api.calls


def test_account_change_invalidates_the_cache(
    api, parse, config, tmp_path, monkeypatch
):
    monkeypatch.delenv("EXOSCALE_API_KEY")
    monkeypatch.delenv("EXOSCALE_API_SECRET")
    cli = two_account_config(tmp_path)
    cache = {
        "cache": True,
        "cache_plugin": "jsonfile",
        "cache_connection": str(tmp_path / "cache"),
        "zones": [ZONE],
        "api_config": str(cli),
    }

    parse(config(accounts=[{"account": "production"}], **cache))
    calls = len(api.calls)
    parse(config(accounts=[{"account": "production"}], **cache))
    assert len(api.calls) == calls

    parse(config(accounts=[{"account": "production"}, {"account": "staging"}], **cache))
    assert len(api.calls) > calls


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

    groups = groups_of(inventory, "web-1")
    assert groups >= {
        ZONE,
        "running",
        "db-spread",
        "instance-pool",
        "pool-1",
        "label_env_dev",
        "instance_b1c6a3a4-0000-4000-8000-000000000001",
    }
    # template, instance type and security groups arrive as ID-only references,
    # so they are not turned into groups at all
    assert not groups & {"medium", "standard", "Linux Ubuntu 22.04 LTS 64-bit", "web"}


def test_zone_qualified_state_group(api, parse, config):
    api.instances[ZONE] = [instance()]

    inventory = parse(config())

    assert f"{ZONE}_running" in groups_of(inventory, "web-1")


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


def test_cli_account_endpoint_reaches_the_client(
    api, parse, config, tmp_path, monkeypatch
):
    monkeypatch.delenv("EXOSCALE_API_KEY")
    monkeypatch.delenv("EXOSCALE_API_SECRET")
    cli = cli_config(
        tmp_path,
        defaultaccount="default",
        accounts=[dict(ACCOUNT, endpoint="https://proxy.example/v2")],
    )

    parse(config(zones=[ZONE], api_config=str(cli)))

    assert ("client", None, "https://proxy.example/v2") in api.calls


def test_api_endpoint_overrides_the_cli_account_endpoint(
    api, parse, config, tmp_path, monkeypatch
):
    monkeypatch.delenv("EXOSCALE_API_KEY")
    monkeypatch.delenv("EXOSCALE_API_SECRET")
    cli = cli_config(
        tmp_path,
        defaultaccount="default",
        accounts=[dict(ACCOUNT, endpoint="https://proxy.example/v2")],
    )

    parse(
        config(
            zones=[ZONE],
            api_config=str(cli),
            api_endpoint="https://option.example/{zone}",
        )
    )

    assert ("client", None, f"https://option.example/{ZONE}") in api.calls


def test_inventory_timeout_environment(api, parse, config, monkeypatch):
    monkeypatch.setenv("EXOSCALE_INVENTORY_TIMEOUT", "5")

    parse(config(zones=[ZONE]))

    api.clients[0].http_client.request(method="get", url="https://api.example")
    assert api.clients[0].http_client.requests[0]["timeout"] == 5


def test_cli_timeout_environment_is_ignored(api, parse, config, monkeypatch):
    # `exo` reads EXOSCALE_TIMEOUT as a duration such as "15s"; inheriting it
    # must not break the integer option.
    monkeypatch.setenv("EXOSCALE_TIMEOUT", "15s")

    parse(config(zones=[ZONE]))

    api.clients[0].http_client.request(method="get", url="https://api.example")
    assert api.clients[0].http_client.requests[0]["timeout"] == 30
