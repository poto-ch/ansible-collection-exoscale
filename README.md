# poto.exoscale

Community Ansible collection for [Exoscale](https://www.exoscale.com/), maintained
by [poto.ch](https://poto.ch); not an Exoscale product. It ships the
`poto.exoscale.exoscale` inventory plugin, which turns Compute instances into
Ansible hosts.

## Requirements

- ansible-core >= 2.15
- Python >= 3.11 (the plugin reads the CLI configuration with `tomllib`)
- [python-exoscale](https://github.com/exoscale/python-exoscale) >= 0.10.0
  (`pip install exoscale`)

## Install

```bash
ansible-galaxy collection install poto.exoscale
```

Or from git:

```bash
ansible-galaxy collection install git+https://github.com/poto-ch/ansible-collection-exoscale.git
```

From a checkout, `devenv shell build` produces `poto-exoscale-<version>.tar.gz`,
which `ansible-galaxy collection install` accepts as a path. In a
`requirements.yml`:

```yaml
collections:
  - name: poto.exoscale
```

## Quickstart

```yaml
# exoscale.yaml
plugin: poto.exoscale.exoscale
zones:
  - ch-gva-2
  - de-fra-1
```

```bash
export EXOSCALE_API_KEY=EXO...
export EXOSCALE_API_SECRET=...
ansible-inventory -i exoscale.yaml --graph
```

The configuration file must be named `exoscale.yaml` or `exoscale.yml`; every
other file is declined by the plugin. `filters` narrows the query server-side
(`labels`, `ip_address`, `manager_id`, `manager_type`); `ansible-doc -t
inventory poto.exoscale.exoscale` lists every option.

## Credentials

Each value is resolved from the plugin option, then the environment, then the
account selected in the [exo CLI](https://github.com/exoscale/cli) configuration:

| Option | Environment | CLI configuration |
| --- | --- | --- |
| `api_key` | `EXOSCALE_API_KEY` | `key` |
| `api_secret` | `EXOSCALE_API_SECRET` | `secret` or `secretCommand` |
| `api_config` | `EXOSCALE_CONFIG` | path to `exoscale.toml` |
| `api_account` | `EXOSCALE_ACCOUNT` | `defaultaccount` |
| `api_timeout` | `EXOSCALE_INVENTORY_TIMEOUT` | — |

`exo config` writes the file and the plugin reads it as-is:

```toml
# ~/.config/exoscale/exoscale.toml
defaultaccount = "production"

[[accounts]]
  key = "EXO..."
  name = "production"
  secret = "EXO..."
```

`api_account` picks another account; `secretCommand` is honoured; a missing file
or account fails the inventory instead of listing nothing. When the credentials
come from the CLI configuration, its `endpoint` is used unless `api_endpoint` is
set. The timeout environment variable is not `EXOSCALE_TIMEOUT`, which `exo`
reads as a duration (`15s`).

## Several accounts

One source can read several CLI accounts, each with its own zones and filters:

```yaml
plugin: poto.exoscale.exoscale
accounts:
  - account: production
    zones: [ch-gva-2, de-fra-1]
  - account: staging
    zones: [ch-gva-2]
    filters:
      labels: "env=staging"
```

`account` is required; an entry's `zones` default to zone discovery and its
`filters` to none. An entry may also name its own `api_config`; the top-level
`zones` and `filters` apply to entries that do not override them. Hosts are
grouped under the account name and tagged with `exoscale_account`, and a name
used in two accounts falls back to the instance ID in both.

Separate config files per account (`-i exoscale-prod.yaml -i exoscale-stg.yaml`)
also work, but the duplicate-name check only sees one source at a time — use
`inventory_hostname: uuid` there.

## Host variables

| Variable | Value |
| --- | --- |
| `exoscale` | the instance as the [v2 API](https://community.exoscale.com/api/compute/) returns it, plus its `zone`, and its `account` when known |
| `exoscale_zone` | the zone the instance lives in |
| `exoscale_account` | the CLI account the host came from, when known |
| `ansible_host`, `ansible_ssh_host` | the address described below |

With `address_family: ipv4` the address is the first attached Elastic IP when
`prefer_elastic_ip` (the default), otherwise `public_ip`; with
`address_family: ipv6` it is `ipv6_address`; with `use_names: true` it is the
instance name. An instance left without an address is dropped from the inventory
unless `show_all: true`.

With `accounts:` every host is labelled with its entry's account. Without it the
label comes from the CLI account the credentials were read from, so
`exoscale_account` is absent only when the credentials come from options or the
environment.

## Automatic groups

| Group | Instance attribute |
| --- | --- |
| `<account>` | the CLI account, when known |
| `<zone>` | `zone` |
| `<state>`, `<zone>_<state>` | `state` |
| `<name>` | each `anti_affinity_groups[]` |
| `<id>`, `instance-pool` | `manager` of type `instance-pool` |
| `label_<key>_<value>` | each `labels` entry |
| `instance_<id>` | `id` |

The group name is the raw value, so zone `ch-gva-2` gives `ch-gva-2_running`.

Ansible sanitises constructed `keyed_groups` instead: `prefix: zone` over
`exoscale_zone` gives `zone_ch_gva_2`. Use `keyed_groups` when the raw names do
not suit the playbook.

## Duplicate names

With `inventory_hostname: name` (the default), a name shared by more than one
instance — in one zone, across zones or across `accounts` — is replaced by the
instance ID for all of them. `inventory_hostname: uuid` always keys hosts by ID.

## Zones and endpoints

`zones` defaults to every zone the API reports, from one unauthenticated
`list-zones` call against `ch-gva-2`; with `accounts:` the default is per entry.
Behind a proxy, set `api_endpoint` with a `{zone}` placeholder and list the zones,
since they cannot be discovered through it:

```yaml
zones: [ch-gva-2, de-fra-1]
api_endpoint: https://exoscale-proxy.example/v2/{zone}
```

Without the placeholder the same endpoint is used for every zone.

## Caching

`cache: true` with `cache_plugin: jsonfile` and a `cache_connection` directory
keeps the per-zone `list-instances` calls out of repeat runs; `expand_hostvars`
adds one `get-instance` call per instance (and with it block storage volumes,
snapshots, SSH keys and cloud-init user-data). The cache key covers the options that
change what is fetched (`accounts`, `api_account`, `zones`, `filters`,
`api_endpoint`, `expand_hostvars`), so editing them refetches.

## Reference

```bash
ansible-doc -t inventory poto.exoscale.exoscale
```

## Tests

```bash
devenv shell test   # or: pytest -q tests/unit
```

`tests/integration/targets/inventory_exoscale` runs `ansible-inventory` against a
real account and checks the documented inventory shape. Its `aliases` mark it
`unsupported`, so a bare `ansible-test integration` skips it; target it by name:

```bash
EXOSCALE_API_KEY=... EXOSCALE_API_SECRET=... \
    ansible-test integration inventory_exoscale
```

## Release

Describe each change in a `changelogs/fragments/<name>.yml` fragment;
`antsibull-changelog release --version <version>` folds them into
`changelogs/changelog.yaml` before you tag. The tool is not part of devenv:
`pipx install antsibull-changelog`.

Push a `v<version>` tag matching `galaxy.yml`: `.github/workflows/release.yml`
runs the checks, builds the tarball, publishes it to Galaxy and attaches it to
the GitHub release. It needs the `GALAXY_API_KEY` repository secret (add
`--server` in the publish step for a private hub).
