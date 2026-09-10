#!/bin/sh

PAK_DIR=${DEVBRIDGE_DIR:-$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)}
PAK_NAME=$(basename "$PAK_DIR")
PAK_NAME=${PAK_NAME%.pak}

cd "$PAK_DIR" || exit 1

: "${SDCARD_PATH:=/mnt/SDCARD}"
: "${PLATFORM:=$(basename "$(dirname "$PAK_DIR")")}"

if [ "$PLATFORM" = "tg3040" ]; then
	: "${DEVICE:=brick}"
	export DEVICE
	export PLATFORM=tg5040
fi

: "${USERDATA_PATH:=$SDCARD_PATH/.userdata/$PLATFORM}"
: "${LOGS_PATH:=$USERDATA_PATH/logs}"
: "${SYSTEM_PATH:=$SDCARD_PATH/.system/$PLATFORM}"

architecture=arm
uname -m 2>/dev/null | grep -q 64 && architecture=arm64

HUMAN_READABLE_NAME="DevBridge"
STATE="$USERDATA_PATH/$PAK_NAME"
LOG_FILE="$LOGS_PATH/$PAK_NAME.txt"
BIN_DIR="$PAK_DIR/bin"
AUTO_SH="$USERDATA_PATH/auto.sh"
BEACON_FLAG="$STATE/beacon-enabled"
SELECTION_FILE="/tmp/${PAK_NAME}-selection.out"
REQUEST_FILE="/tmp/${PAK_NAME}-request"

# How often the status is re-read. The screen is only redrawn when one of the
# watched values actually changes, so this can be brisk without the display
# flickering while it is left open.
POLL_SECONDS=${DEVBRIDGE_POLL:-3}
LAST_SELECTED=0

# BusyBox sleep only accepts fractions when built with float support. Probe
# once: a sub-second tick keeps button presses feeling immediate, and a whole
# second is the safe fallback.
SLEEP_TICK=1
sleep 0.2 2>/dev/null && SLEEP_TICK=0.2

mkdir -p "$STATE" "$LOGS_PATH"
[ ! -f "$LOG_FILE" ] || mv "$LOG_FILE" "$LOG_FILE.1"
: > "$LOG_FILE"

export HOME="$STATE"
export PAK_DIR
export DEVBRIDGE_STATE="$STATE"
export PATH="$BIN_DIR/$PLATFORM:$BIN_DIR/$architecture:$BIN_DIR:$PATH"
export LD_LIBRARY_PATH="$PAK_DIR/lib:$SYSTEM_PATH/lib:${LD_LIBRARY_PATH:-}"

. "$BIN_DIR/devbridge.sh" || exit 1

# NextUI starts every pak in performance mode. DevBridge spends almost all of
# its life waiting for input or a three-second status poll, so return to the
# platform's menu/Auto profile immediately: schedutil on tg5040 and the A30's
# conservative Auto profile. The firmware helper owns the exact frequency
# bounds and avoids duplicating platform policy here.
set_cpu_auto() {
	governor="$SYSTEM_PATH/bin/governor.sh"
	[ -x "$governor" ] || return 0
	sh "$governor" auto >/dev/null 2>&1 || true
}

log() {
	printf '%s %s\n' "$(date '+%H:%M:%S' 2>/dev/null)" "$*" >> "$LOG_FILE"
}

# NextUI keeps the screen awake through minui-list's --disable-auto-sleep,
# which calls PWR_disableAutosleep() for the lifetime of that process. The
# stay_awake file is a MinUI-era convention that NextUI ignores; it is written
# only so the pak behaves on firmwares that do honour it.
hold_screen_awake() {
	echo 1 > /tmp/stay_awake 2>/dev/null || true
}

service_running() {
	"$BIN_DIR/service-is-running" >/dev/null 2>&1
}

service_provider() {
	ssh_provider
}

beacon_enabled() {
	[ -f "$BEACON_FLAG" ]
}

beacon_running() {
	[ -f "$STATE/beacon.pid" ] || return 1
	pid=$(cat "$STATE/beacon.pid" 2>/dev/null)
	case "$pid" in ''|*[!0-9]*) return 1 ;; esac
	[ -e "/proc/$pid" ]
}

will_start_on_boot() {
	grep -q "${PAK_NAME}.pak-on-boot" "$AUTO_SH" 2>/dev/null
}

enable_start_on_boot() {
	if [ ! -f "$AUTO_SH" ]; then
		printf '#!/bin/sh\n\n' > "$AUTO_SH"
	fi
	printf 'test -f "$SDCARD_PATH/Tools/$PLATFORM/%s.pak/bin/on-boot" && "$SDCARD_PATH/Tools/$PLATFORM/%s.pak/bin/on-boot" & # %s.pak-on-boot\n' \
		"$PAK_NAME" "$PAK_NAME" "$PAK_NAME" >> "$AUTO_SH"
	chmod +x "$AUTO_SH"
	sync
}

disable_start_on_boot() {
	[ -f "$AUTO_SH" ] || return 0
	sed -i "/${PAK_NAME}.pak-on-boot/d" "$AUTO_SH"
	sync
}

sanitize() {
	printf '%s' "$1" | tr '\t\r\n' '   '
}

add_row() {
	printf '%s\t%s\t%s\n' "$(sanitize "$1")" "$(sanitize "$2")" "${3:-0}" >> "$MENU_ROWS"
}

connection_address() {
	address=$(ip_address 2>/dev/null) || return 1
	printf '%s\n' "$address"
}

# state_token is the cheap comparison the refresh loop watches; the screen is
# redrawn only when it changes.
state_token() {
	printf '%s|%s|%s|%s|%s|%s|%s|%s\n' \
		"$(service_provider)" \
		"$(ssh_service_port)" \
		"$(sftp_available && echo sftp || echo nosftp)" \
		"$(connection_address 2>/dev/null)" \
		"$(battery_percent 2>/dev/null)" \
		"$(charge_status 2>/dev/null)" \
		"$(will_start_on_boot && echo boot || echo noboot)" \
		"$(beacon_enabled && echo beacon || echo nobeacon)"
}

build_menu() {
	MENU_ROWS="/tmp/${PAK_NAME}-rows.tsv"
	menu="/tmp/${PAK_NAME}-menu.json"
	rm -f "$MENU_ROWS" "$menu"

	provider=$(service_provider)
	case "$provider" in
		system) add_row "Status:" "System SSH" ;;
		bundled) add_row "Status:" "DevBridge SSH" ;;
		*) add_row "Status:" "Stopped" ;;
	esac

	address=$(connection_address 2>/dev/null)
	if [ -n "$address" ]; then
		add_row "Address:" "$address"
		if service_running; then
			active_port=$(ssh_service_port)
			if [ "$active_port" = 22 ]; then
				add_row "SSH:" "ssh root@$address"
			else
				add_row "SSH:" "ssh -p $active_port root@$address"
			fi
			if sftp_available; then
				if [ "$active_port" = 22 ]; then
					add_row "SFTP:" "sftp://root@$address"
				else
					add_row "SFTP:" "sftp://root@$address:$active_port"
				fi
			else
				add_row "SFTP:" "Unavailable"
			fi
		fi
	else
		add_row "Address:" "No network"
	fi

	add_row "Host:" "$(device_hostname 2>/dev/null || echo 'Unknown')"
	add_row "Battery:" "$(battery_percent 2>/dev/null || echo 'Unknown')"
	add_row "Power:" "$(charge_status 2>/dev/null || echo 'Unknown')"

	if beacon_enabled; then
		if beacon_running; then
			add_row "Discovery:" "Broadcasting"
		else
			add_row "Discovery:" "Enabled (idle)"
		fi
	else
		add_row "Discovery:" "Off"
	fi

	add_row "Updated:" "$(date '+%H:%M:%S' 2>/dev/null || echo '')"
	add_row " " ""

	if [ "$provider" = bundled ]; then
		add_row "Stop DevBridge" "" 1
	elif [ "$provider" = none ]; then
		add_row "Start DevBridge" "" 1
	fi
	add_row "Start on boot: $(will_start_on_boot && echo On || echo Off)" "" 1
	add_row "Discovery beacon: $(beacon_enabled && echo On || echo Off)" "" 1

	jq -Rsc '
		split("\n")
		| map(select(length > 0) | split("\t")
			| {name: .[0]}
				+ (if (.[1] // "") == "" then {} else {options: [.[1]]} end)
				+ {features: {unselectable: (.[2] != "1")}})
		| {items: .}' "$MENU_ROWS" > "$menu" || return 1

	printf '%s\n' "$menu"
}

first_action_index() {
	jq -r '[.items[] | .features.unselectable] | index(false) // 0' "$1"
}

# minui-list has no timeout or reload signal, so refreshing means terminating
# and relaunching it. It handles SIGTERM cleanly and exits 143, which the
# caller reads as "redraw, no user action". The exit code travels through a
# file because `kill -0` cannot tell a live process from an unreaped zombie.
main_screen() {
	menu="$1"
	rc_file="/tmp/${PAK_NAME}-list.rc"
	pid_file="/tmp/${PAK_NAME}-list.pid"
	rm -f "$SELECTION_FILE" "$rc_file" "$pid_file"

	selected="$LAST_SELECTED"
	[ "$selected" -gt 0 ] 2>/dev/null || selected=$(first_action_index "$menu")

	{
		minui-list --disable-auto-sleep --file "$menu" --format json \
			--title "$HUMAN_READABLE_NAME" --title-alignment center \
			--confirm-text "SELECT" --cancel-text "EXIT" --item-key items \
			--selected "$selected" \
			--write-location "$SELECTION_FILE" >> "$LOG_FILE" 2>&1 &
		printf '%s\n' $! > "$pid_file"
		list_rc=0
		wait $! || list_rc=$?
		printf '%s\n' "$list_rc" > "$rc_file"
	} 2>> "$LOG_FILE" &

	# Ticks fast so a button press is noticed promptly, but only evaluates the
	# status every POLL_SECONDS, and only redraws when something changed.
	baseline=$(state_token)
	checked=$(date +%s 2>/dev/null || echo 0)
	while [ ! -f "$rc_file" ]; do
		sleep "$SLEEP_TICK"
		[ -f "$REQUEST_FILE" ] && break
		now=$(date +%s 2>/dev/null || echo 0)
		[ $((now - checked)) -lt "$POLL_SECONDS" ] && continue
		checked="$now"
		[ "$(state_token)" != "$baseline" ] && break
	done

	if [ ! -f "$rc_file" ]; then
		pid=$(cat "$pid_file" 2>/dev/null)
		[ -n "$pid" ] && kill "$pid" 2>/dev/null
		waited=0
		while [ ! -f "$rc_file" ] && [ "$waited" -lt 5 ]; do
			sleep 1
			waited=$((waited + 1))
		done
	fi

	if [ -f "$REQUEST_FILE" ]; then
		return 200
	fi

	rc=$(cat "$rc_file" 2>/dev/null)
	case "$rc" in ''|*[!0-9]*) rc=143 ;; esac
	[ "$rc" = 143 ] || { [ -f "$SELECTION_FILE" ] && cat "$SELECTION_FILE"; }
	return "$rc"
}

remember_selection() {
	menu="$1"
	choice="$2"
	index=$(jq -r --arg name "$choice" \
		'[.items[] | .name] | index($name) // 0' "$menu" 2>/dev/null)
	case "$index" in ''|*[!0-9]*) return 0 ;; esac
	LAST_SELECTED="$index"
}

start_service() {
	log "starting service"
	if "$BIN_DIR/service-on"; then
		log "service started"
	else
		log "service failed to start; see $LOGS_PATH/$PAK_NAME.service.txt"
	fi
}

stop_service() {
	log "stopping service"
	"$BIN_DIR/service-off" >/dev/null 2>&1
}

toggle_boot() {
	if will_start_on_boot; then
		disable_start_on_boot
		log "start on boot disabled"
	else
		enable_start_on_boot
		log "start on boot enabled"
	fi
}

toggle_beacon() {
	if beacon_enabled; then
		rm -f "$BEACON_FLAG"
		kill_beacon
		log "discovery beacon disabled"
		return
	fi

	touch "$BEACON_FLAG"
	log "discovery beacon enabled"
	if service_running && ! beacon_running; then
		"$BIN_DIR/beacon.sh" >> "$LOG_FILE" 2>&1 &
	fi
}

kill_beacon() {
	[ -f "$STATE/beacon.pid" ] || return 0
	pid=$(cat "$STATE/beacon.pid" 2>/dev/null)
	case "$pid" in ''|*[!0-9]*) return 0 ;; esac
	kill "$pid" 2>/dev/null
	rm -f "$STATE/beacon.pid"
}

cleanup() {
	rm -f "/tmp/${PAK_NAME}-rows.tsv" "/tmp/${PAK_NAME}-menu.json" \
		"/tmp/${PAK_NAME}-list.rc" "/tmp/${PAK_NAME}-list.pid" \
		"$SELECTION_FILE" /tmp/stay_awake
}

main() {
	trap cleanup EXIT INT TERM HUP QUIT
	set_cpu_auto
	hold_screen_awake

	command -v minui-list >/dev/null 2>&1 && command -v jq >/dev/null 2>&1 || {
		log "minui-list or jq is missing for $PLATFORM"
		printf '%s\n' "DevBridge UI is missing for $PLATFORM" >&2
		return 1
	}

	for executable in "$BIN_DIR/$PLATFORM/minui-list" "$BIN_DIR/$PLATFORM/jq" \
		"$BIN_DIR/service-on" "$BIN_DIR/service-off" \
		"$BIN_DIR/service-is-running" "$BIN_DIR/on-boot" \
		"$BIN_DIR/input" "$BIN_DIR/input-on" \
		"$BIN_DIR/protocol" "$BIN_DIR/game-launch" \
		"$BIN_DIR/shell-wrapper.sh" "$BIN_DIR/beacon.sh" \
		"$BIN_DIR/$architecture/dropbearmulti" \
		"$BIN_DIR/$architecture/gesftpserver"; do
		chmod +x "$executable" 2>/dev/null || true
	done
	"$BIN_DIR/input-on"

	log "$PAK_NAME started on $PLATFORM"

	while :; do
		menu=$(build_menu) || { log "could not build the menu"; return 1; }
		selection=$(main_screen "$menu")
		rc=$?
		case "$rc" in
			200)
				request=$(cat "$REQUEST_FILE" 2>/dev/null)
				rm -f "$REQUEST_FILE"
				[ -n "$request" ] || { log "empty protocol launch request"; continue; }
				log "protocol launch starting"
				# We are already inside NextUI's /tmp/next transaction. NextUI
				# deletes that file after this pak returns, so a nested handoff
				# would be discarded. Replace this UI process with the validated
				# wrapper and let NextUI keep waiting normally.
				cleanup
				trap - EXIT INT TERM HUP QUIT
				exec /bin/sh -c "$request"
				;;
			0)
				remember_selection "$menu" "$selection"
				case "$selection" in
					"Start DevBridge") start_service ;;
					"Stop DevBridge") stop_service ;;
					"Start on boot:"*) toggle_boot ;;
					"Discovery beacon:"*) toggle_beacon ;;
				esac
				;;
			143) : ;; # a watched value changed, redraw
			2|3) break ;;
			*) log "minui-list exited with $rc"; break ;;
		esac
	done
}

if [ "${DEVBRIDGE_SOURCE_ONLY:-0}" != 1 ]; then
	main "$@"
fi
