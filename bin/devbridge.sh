#!/bin/sh

# Shared helpers for DevBridge, sourced by launch.sh, the service scripts and
# the host-side tests. Nothing here may assume the A30: every my282-specific
# path degrades to a portable fallback so the pak still reports something
# useful on other hardware.

: "${POWER_SUPPLY_ROOT:=/sys/class/power_supply}"
: "${NET_ROOT:=/sys/class/net}"
: "${AXP_REG_NODE:=/sys/devices/platform/axp22_board/axp22_reg}"

: "${BEACON_GROUP:=239.255.42.99}"
: "${BEACON_PORT:=42099}"
: "${BEACON_INTERVAL:=5}"
: "${SSH_PORT:=22}"
: "${SSH_FALLBACK_PORT:=2222}"

read_value() {
	[ -r "$1" ] || return 1
	IFS= read -r value < "$1" || [ -n "$value" ]
	printf '%s\n' "$value"
}

# --- architecture ----------------------------------------------------------

passwd_is_bound() {
	grep -q ' /etc/passwd ' "${PROC_MOUNTS:-/proc/mounts}" 2>/dev/null
}

# Ownership is decided by comparing content, not by trusting the marker file.
# The marker lives on the SD card and outlives both a reboot and any other
# pak's mount, so on its own it will happily claim a bind that belongs to the
# SSH Server pak -- and unmounting that would break somebody else's service.
passwd_is_ours() {
	passwd_is_bound || return 1
	[ -r "${ETC_PASSWD:-/etc/passwd}" ] || return 1
	[ "$(cat "${ETC_PASSWD:-/etc/passwd}" 2>/dev/null)" = \
		"$(cat "$PAK_DIR/res/passwd" 2>/dev/null)" ]
}

arch_dir() {
	if uname -m 2>/dev/null | grep -q 64; then
		printf 'arm64\n'
	else
		printf 'arm\n'
	fi
}

arch_bin() {
	printf '%s/%s/%s\n' "${BIN_DIR:-$PAK_DIR/bin}" "$(arch_dir)" "$1"
}

# The armv7 build is Dropbear's multi-call binary and needs a subcommand; the
# aarch64 build is a plain dropbear. Emits an unquoted command line, so the
# caller must leave it unquoted for word splitting.
dropbear_command() {
	multi=$(arch_bin dropbearmulti)
	single=$(arch_bin dropbear)
	if [ -x "$multi" ]; then
		printf '%s dropbear\n' "$multi"
	elif [ -x "$single" ]; then
		printf '%s\n' "$single"
	else
		return 1
	fi
}

# --- SSH/SFTP service discovery -------------------------------------------

ssh_port_is_listening() {
	checked_port=${1:-$SSH_PORT}
	command -v netstat >/dev/null 2>&1 || return 1
	netstat -ltn 2>/dev/null | grep -q "[:.]${checked_port}[ \t]"
}

bundled_ssh_running() {
	pid_file="${DEVBRIDGE_STATE:-${USERDATA_PATH:-/tmp}/DevBridge}/dropbear.pid"
	[ -f "$pid_file" ] || return 1
	pid=$(cat "$pid_file" 2>/dev/null)
	case "$pid" in ''|*[!0-9]*) return 1 ;; esac
	[ -e "/proc/$pid" ] || return 1
	tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null |
		grep -qF "${PAK_DIR}/bin/"
}

# Reports who owns the SSH endpoint without treating another server as ours.
# The system provider includes firmware OpenSSH and any externally-managed SSH
# service already listening when DevBridge starts.
ssh_provider() {
	if bundled_ssh_running; then
		printf 'bundled\n'
	elif ssh_port_is_listening; then
		printf 'system\n'
	else
		printf 'none\n'
	fi
}

ssh_service_running() {
	[ "$(ssh_provider)" != none ]
}

ssh_service_port() {
	if bundled_ssh_running; then
		port_file="${DEVBRIDGE_STATE:-${USERDATA_PATH:-/tmp}/DevBridge}/ssh-port"
		if [ -r "$port_file" ]; then
			active_port=$(cat "$port_file" 2>/dev/null)
			case "$active_port" in
				''|*[!0-9]*) : ;;
				*) printf '%s\n' "$active_port"; return 0 ;;
			esac
		fi
	fi
	printf '%s\n' "$SSH_PORT"
}

# Read OpenSSH's configured SFTP subsystem. The stock Brick firmware uses an
# absolute external server; internal-sftp is also valid and needs no file.
openssh_sftp_target() {
	for config in ${SSHD_CONFIG_GLOB:-/etc/ssh/sshd_config /etc/ssh/sshd_config.d/*.conf}; do
		[ -r "$config" ] || continue
		awk '
			tolower($1) == "subsystem" && tolower($2) == "sftp" {
				print $3
				found = 1
				exit 0
			}
			END { if (!found) exit 1 }
		' "$config" 2>/dev/null && return 0
	done
	return 1
}

# Firmware Dropbear (H700 Base OS) has no config file: its SFTP subsystem is
# the compiled-in /usr/libexec/sftp-server, so it is usable exactly when that
# file is executable.
dropbear_sftp_target() {
	pidof dropbear >/dev/null 2>&1 || pidof dropbearmulti >/dev/null 2>&1 ||
		return 1
	target=${DROPBEAR_SFTP_SERVER:-/usr/libexec/sftp-server}
	[ -x "$target" ] || return 1
	printf '%s\n' "$target"
}

system_sftp_available() {
	dropbear_sftp_target >/dev/null 2>&1 && ! bundled_ssh_running && return 0
	target=$(openssh_sftp_target 2>/dev/null) || return 1
	case "$target" in
		internal-sftp) return 0 ;;
		/*) [ -x "$target" ] ;;
		'') return 1 ;;
		*) command -v "$target" >/dev/null 2>&1 ;;
	esac
}

sftp_available() {
	case "$(ssh_provider)" in
		bundled) [ -x "$(arch_bin gesftpserver)" ] ;;
		system) system_sftp_available ;;
		*) return 1 ;;
	esac
}

# --- battery ---------------------------------------------------------------

find_battery() {
	for supply in "$POWER_SUPPLY_ROOT"/*; do
		[ -d "$supply" ] || continue
		type=$(read_value "$supply/type" 2>/dev/null) || continue
		[ "$type" = "Battery" ] || continue
		printf '%s\n' "$supply"
		return 0
	done
	return 1
}

battery_percent() {
	path=$(find_battery) || return 1
	value=$(read_value "$path/capacity" 2>/dev/null) || return 1
	case "$value" in ''|*[!0-9]*) return 1 ;; esac
	printf '%s%%\n' "$value"
}

axp_available() {
	[ -r "$AXP_REG_NODE" ] && [ -w "$AXP_REG_NODE" ]
}

axp_read_reg() {
	axp_available || return 1
	printf '%s\n' "$1" > "$AXP_REG_NODE" 2>/dev/null || return 1
	value=$(cat "$AXP_REG_NODE" 2>/dev/null) || return 1
	value=${value##*=}
	case "$value" in ''|*[!0-9A-Fa-f]*) return 1 ;; esac
	printf '%s\n' "$value"
}

# The A30's driver-reported status is derived from "input present AND charging
# enabled" rather than from real current direction, so it reports Charging
# while the battery drains. Register 0x00 bit2 is the ground truth, and bit7
# reports whether a charger is attached at all.
decode_charge_state() {
	value="$1"
	case "$value" in ''|*[!0-9A-Fa-f]*) return 1 ;; esac
	value=$((0x$value))
	if [ $((value & 0x80)) -ne 0 ]; then
		if [ $((value & 0x04)) -ne 0 ]; then
			printf 'Charging\n'
		else
			printf 'Connected (not charging)\n'
		fi
	else
		printf 'Discharging\n'
	fi
}

charge_status() {
	decode_charge_state "$(axp_read_reg 0x00 2>/dev/null)" && return 0
	path=$(find_battery) || return 1
	read_value "$path/status" 2>/dev/null
}

# --- network ---------------------------------------------------------------

wifi_interface() {
	for candidate in wlan0 wlan1 eth0; do
		[ -d "$NET_ROOT/$candidate" ] && { printf '%s\n' "$candidate"; return 0; }
	done
	return 1
}

ip_address() {
	interface=$(wifi_interface) || return 1
	address=$(ip addr show "$interface" 2>/dev/null |
		sed -n 's#.*inet \([0-9.]*\)/.*#\1#p' | head -n 1)
	if [ -z "$address" ]; then
		address=$(ifconfig "$interface" 2>/dev/null |
			sed -n 's/.*inet addr:\([0-9.]*\).*/\1/p' | head -n 1)
	fi
	[ -n "$address" ] || return 1
	printf '%s\n' "$address"
}

device_hostname() {
	hostname 2>/dev/null || read_value /proc/sys/kernel/hostname 2>/dev/null
}

# --- discovery beacon ------------------------------------------------------
#
# The beacon targets a multicast group rather than a broadcast address so no
# SO_BROADCAST is required and the traffic stays on the local link.
#
# Transport is the bundled udpsend: BusyBox nc on both devices is built
# without FEATURE_NETCAT_EXTRA (no -u, so no UDP at all), and neither ships
# socat, python or perl. The A30 has no bash either, ruling out /dev/udp.

beacon_transport() {
	sender=$(arch_bin udpsend)
	[ -x "$sender" ] && { printf '%s\n' "$sender"; return 0; }
	return 1
}

beacon_supported() {
	beacon_transport >/dev/null 2>&1
}

# The payload is space-separated key=value, so every value must be free of
# spaces; "Connected (not charging)" would otherwise split into three tokens
# and corrupt the fields after it.
charge_token() {
	case "$(charge_status 2>/dev/null)" in
		Charging) printf 'charging\n' ;;
		"Connected (not charging)") printf 'connected-not-charging\n' ;;
		Discharging) printf 'discharging\n' ;;
		'') printf 'unknown\n' ;;
		*) charge_status 2>/dev/null | tr ' ' '-' ;;
	esac
}

beacon_payload() {
	printf 'devbridge host=%s ip=%s port=%s battery=%s charge=%s sftp=%s\n' \
		"$(device_hostname 2>/dev/null || echo unknown)" \
		"$(ip_address 2>/dev/null || echo unknown)" \
		"$(ssh_service_port)" \
		"$(battery_percent 2>/dev/null || echo unknown)" \
		"$(charge_token)" \
		"$(sftp_available && echo yes || echo no)"
}

beacon_send() {
	sender=$(beacon_transport) || return 1
	printf '%s\n' "$1" | "$sender" "$BEACON_GROUP" "$BEACON_PORT" >/dev/null 2>&1
}
