# poto.exoscale

Community Ansible collection for [Exoscale](https://www.exoscale.com/), maintained
by [poto.ch](https://poto.ch); not an Exoscale product. It ships the
`poto.exoscale.exoscale` inventory plugin, which turns Compute instances into
Ansible hosts.

## Requirements

- ansible-core >= 2.15
- [python-exoscale](https://github.com/exoscale/python-exoscale) >= 0.10.0
  (`pip install exoscale`)

## Install

```bash
ansible-galaxy collection install git+https://github.com/poto-ch/ansible-collection-exoscale.git
```

From a checkout: `devenv shell build`, then
`ansible-galaxy collection install poto-exoscale-*.tar.gz`.

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
other file is declined by the plugin.

## Credentials

Every value is resolved from the plugin option, then the environment, then an
INI section:

| Option | Environment | INI key |
| --- | --- | --- |
| `api_key` | `EXOSCALE_API_KEY` | `key` |
| `api_secret` | `EXOSCALE_API_SECRET` | `secret` |
| `api_config` | `EXOSCALE_CONFIG` | — |
| `api_profile` | `EXOSCALE_PROFILE` | — |

```ini
# ~/.exoscale.ini or ./exoscale.ini
[default]
key = EXO...
secret = ...

[production]
key = EXO...
secret = ...
```

`api_profile` picks the section, `api_config` the file. Missing credentials fail
the inventory instead of listing nothing.

## Host variables

| Variable | Value |
| --- | --- |
| `exoscale` | the instance as the [v2 API](https://community.exoscale.com/api/compute/) returns it, plus its `zone` |
| `exoscale_zone` | the zone the instance lives in |
| `ansible_host`, `ansible_ssh_host` | the address described below |

With `address_family: ipv4` the address is the first attached Elastic IP when
`prefer_elastic_ip` (the default), otherwise `public_ip`; with
`address_family: ipv6` it is `ipv6_address`; with `use_names: true` it is the
instance name. An instance left without an address is dropped from the inventory
unless `show_all: true`.

## Automatic groups

| Group | Instance attribute |
| --- | --- |
| `<zone>` | `zone` |
| `<state>`, `<zone>_<state>` | `state` |
| `<size>`, `<family>` | `instance_type` |
| `<name>`, `<zone>_<name>` | `template.name` |
| `<name>` | each `security_groups[]` |
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
instance — in one zone or across zones — is replaced by the instance ID for all
of them. `inventory_hostname: uuid` always keys hosts by ID.

## Zones and endpoints

`zones` defaults to every zone the API reports, from one unauthenticated
`list-zones` call against `ch-gva-2`. Behind a proxy, set `api_endpoint` with a
`{zone}` placeholder and list the zones, since they cannot be discovered through
it:

```yaml
zones: [ch-gva-2, de-fra-1]
api_endpoint: https://exoscale-proxy.example/v2/{zone}
```

## Caching

`cache: true` with `cache_plugin: jsonfile` and a `cache_connection` directory
keeps the per-zone `list-instances` calls out of repeat runs. `expand_hostvars`
adds one `get-instance` call per instance.

## Reference

```bash
ansible-doc -t inventory poto.exoscale.exoscale
```

## Tests

```bash
devenv shell test   # or: pytest -q tests/unit
```

## Release

Describe each change in a `changelogs/fragments/<name>.yml` fragment; `ansible-galaxy`
publishes what `antsibull-changelog release --version <version>` folds into
`changelogs/changelog.yaml`.

Push a `v<version>` tag matching `galaxy.yml`: `.github/workflows/release.yml`
runs the checks, builds the tarball, publishes it to Galaxy and attaches it to
the GitHub release. It needs the `GALAXY_API_KEY` repository secret (add
`--server` in the publish step for a private hub).
