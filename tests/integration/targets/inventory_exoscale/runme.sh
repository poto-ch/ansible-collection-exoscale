#!/usr/bin/env bash
# Runs against a real Exoscale account, so CI skips it (see aliases):
#       ansible-test integration inventory_exoscale
# Credentials come from EXOSCALE_API_KEY/EXOSCALE_API_SECRET or from the exo CLI
# configuration; without either the plugin fails and so does this script.
# Set EXOSCALE_ZONE to query another zone (default: ch-gva-2).
set -eux

zone="${EXOSCALE_ZONE:-ch-gva-2}"

workdir="$(mktemp -d)"
trap 'rm -rf "${workdir}"' EXIT
cd "${workdir}"

cat > exoscale.yaml <<EOF
plugin: poto.exoscale.exoscale
zones: [${zone}]
EOF

ansible-inventory -i exoscale.yaml --graph
ansible-inventory -i exoscale.yaml --list > inventory.json

# The documented shape: every host carries the instance and its zone, and an
# address only when one could be resolved.
python - <<'PY'
import json


def plain(value):
    """Unwrap ansible-core's marker for values the plugin did not tag as trusted."""
    if isinstance(value, dict) and set(value) == {"__ansible_unsafe"}:
        return value["__ansible_unsafe"]
    return value


with open("inventory.json") as f:
    inventory = json.load(f)

hostvars = inventory["_meta"]["hostvars"]
for host, variables in hostvars.items():
    zone = plain(variables["exoscale_zone"])
    assert isinstance(zone, str) and zone, host
    assert plain(variables["exoscale"]["zone"]) == zone, host
    if "exoscale_account" in variables:
        account = plain(variables["exoscale_account"])
        assert isinstance(account, str) and account, host
    for key in ("ansible_host", "ansible_ssh_host"):
        if key in variables:
            address = plain(variables[key])
            assert isinstance(address, str) and address, (host, key)

print(f"{len(hostvars)} host(s) with the documented hostvars")
PY
