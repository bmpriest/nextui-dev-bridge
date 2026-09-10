#!/usr/bin/env bash

# Fallback discovery for when the beacon is unavailable: sweep the local /24
# for hosts with port 22 or DevBridge's port-2222 fallback open. Needs nothing
# on the device, so it works even when the beacon is unavailable.
#
#   devbridge-find.sh              # sweep the subnet of the default route
#   devbridge-find.sh 192.168.1    # sweep a specific /24 prefix

set -uo pipefail

PORTS=${DEVBRIDGE_PORTS:-${DEVBRIDGE_PORT:-22 2222}}
TIMEOUT=${DEVBRIDGE_TIMEOUT:-0.3}

prefix=${1:-}
if [[ -z "$prefix" ]]; then
	address=$(ip route get 1.1.1.1 2>/dev/null | sed -n 's/.*src \([0-9.]*\).*/\1/p')
	if [[ -z "$address" ]]; then
		echo "could not determine the local subnet; pass one, e.g. 192.168.1" >&2
		exit 1
	fi
	prefix=${address%.*}
fi

echo "sweeping ${prefix}.0/24 for ports ${PORTS}..." >&2

for host in $(seq 1 254); do
	(
		target="${prefix}.${host}"
		for port in $PORTS; do
			if timeout "$TIMEOUT" bash -c "exec 3<>/dev/tcp/${target}/${port}" 2>/dev/null; then
				# Grab the banner so a NextUI device is distinguishable from a
				# router or NAS answering on the same port.
				banner=$(timeout "$TIMEOUT" bash -c \
					"exec 3<>/dev/tcp/${target}/${port}; head -c 64 <&3" 2>/dev/null |
					tr -d '\r\n')
				printf '%-20s %s\n' "${target}:${port}" "${banner:-open}"
			fi
		done
	) &
done
wait
