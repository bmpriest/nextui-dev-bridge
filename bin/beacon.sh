#!/bin/sh

# Multicast discovery beacon. Announces the device's address and state on the
# local network every BEACON_INTERVAL seconds so a host does not have to guess
# the IP after a DHCP lease change. Run detached by service-on.

BIN_DIR="$(dirname "$0")"
PAK_DIR="$(dirname "$BIN_DIR")"

. "$BIN_DIR/devbridge.sh" || exit 1

PID_FILE="${DEVBRIDGE_STATE:-/tmp}/beacon.pid"
IP_FILE="${DEVBRIDGE_IP_FILE:-/mnt/SDCARD/DEVBRIDGE-IP.txt}"

printf '%s\n' "$$" > "$PID_FILE"
trap 'rm -f "$PID_FILE"; exit 0' EXIT INT TERM HUP QUIT

while :; do
	payload=$(beacon_payload)
	beacon_send "$payload"

	# Written unconditionally: if multicast is blocked by the network, or nc
	# lacks UDP support, the card itself still carries the current address.
	address=$(ip_address 2>/dev/null) || address=""
	if [ -n "$address" ]; then
		{
			printf '%s\n' "$payload"
			printf 'updated=%s\n' "$(date 2>/dev/null)"
		} > "$IP_FILE" 2>/dev/null
	fi

	sleep "$BEACON_INTERVAL"
done
