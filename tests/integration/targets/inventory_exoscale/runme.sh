#!/usr/bin/env bash
# Runs against a real Exoscale account, so CI skips it (see aliases):
#   EXOSCALE_API_KEY=... EXOSCALE_API_SECRET=... \
#       ansible-test integration inventory_exoscale
# Set EXOSCALE_ZONE to query another zone (default: ch-gva-2).
set -eux

: "${EXOSCALE_API_KEY:?set EXOSCALE_API_KEY}"
: "${EXOSCALE_API_SECRET:?set EXOSCALE_API_SECRET}"
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

with open("inventory.json") as f:
    inventory = json.load(f)

hostvars = inventory["_meta"]["hostvars"]
for host, variables in hostvars.items():
    assert variables["exoscale_zone"], host
    assert variables["exoscale"]["zone"] == variables["exoscale_zone"], host
    for key in ("ansible_host", "ansible_ssh_host"):
        if key in variables:
            assert isinstance(variables[key], str) and variables[key], (host, key)

print(f"{len(hostvars)} host(s) with the documented hostvars")
PY
