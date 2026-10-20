#!/bin/sh

set -eu

DIR=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
FIXTURE=$(mktemp -d)
trap 'rm -rf "$FIXTURE"' EXIT INT TERM

expect_failure() {
	if "$@"; then
		printf 'expected failure: %s\n' "$*" >&2
		exit 1
	fi
}

mkdir -p "$FIXTURE/power/battery0" "$FIXTURE/net/wlan0" "$FIXTURE/stub" \
	"$FIXTURE/sd/.userdata/my282/logs"
printf '%s\n' Battery > "$FIXTURE/power/battery0/type"
printf '%s\n' 64 > "$FIXTURE/power/battery0/capacity"
printf '%s\n' Discharging > "$FIXTURE/power/battery0/status"

cat > "$FIXTURE/stub/ip" <<'STUB'
#!/bin/sh
echo "    inet 192.168.0.166/24 brd 192.168.0.255 scope global wlan0"
STUB
cat > "$FIXTURE/stub/hostname" <<'STUB'
#!/bin/sh
echo MIYOO282
STUB
cat > "$FIXTURE/stub/netstat" <<STUB
#!/bin/sh
[ ! -f "$FIXTURE/stub/port-22-up" ] || echo 'tcp 0 0 0.0.0.0:22 0.0.0.0:* LISTEN'
[ ! -f "$FIXTURE/stub/port-2222-up" ] || echo 'tcp 0 0 0.0.0.0:2222 0.0.0.0:* LISTEN'
STUB
REAL_PIDOF=$(command -v pidof || echo false)
cat > "$FIXTURE/stub/pidof" <<STUB
#!/bin/sh
case "\$1" in
	dropbear|dropbearmulti) [ -f "$FIXTURE/stub/dropbear-up" ] ;;
	*) exec "$REAL_PIDOF" "\$@" ;;
esac
STUB
chmod +x "$FIXTURE/stub/ip" "$FIXTURE/stub/hostname" "$FIXTURE/stub/netstat" \
	"$FIXTURE/stub/pidof"

# A fake pak tree so the arch-resolving helpers can be exercised on the host.
: > "$FIXTURE/stub/udp-log"
for arch in arm arm64; do
	mkdir -p "$FIXTURE/bin/$arch"
	cat > "$FIXTURE/bin/$arch/udpsend" <<STUB
#!/bin/sh
printf '%s %s ' "\$1" "\$2" >> "$FIXTURE/stub/udp-log"
cat >> "$FIXTURE/stub/udp-log"
exit 0
STUB
	: > "$FIXTURE/bin/$arch/gesftpserver"
	chmod +x "$FIXTURE/bin/$arch/udpsend" "$FIXTURE/bin/$arch/gesftpserver"
done

POWER_SUPPLY_ROOT="$FIXTURE/power"
NET_ROOT="$FIXTURE/net"
AXP_REG_NODE="$FIXTURE/absent-axp-node"
export POWER_SUPPLY_ROOT NET_ROOT AXP_REG_NODE

. "$DIR/bin/devbridge.sh"

[ "$(find_battery)" = "$FIXTURE/power/battery0" ]
[ "$(battery_percent)" = "64%" ]

# Register decoding: 0xc4 charging, 0xc0 the attached-but-not-charging fault,
# 0x00 unplugged. 0x80 is a charger present without a valid input.
[ "$(decode_charge_state c4)" = "Charging" ]
[ "$(decode_charge_state c0)" = "Connected (not charging)" ]
[ "$(decode_charge_state 00)" = "Discharging" ]
[ "$(decode_charge_state 80)" = "Connected (not charging)" ]
expect_failure decode_charge_state ""
expect_failure decode_charge_state zz

# With no PMU node present the status must fall back to the driver attribute
# instead of failing, so non-A30 hardware still reports something.
[ "$(charge_status)" = "Discharging" ]

PATH="$FIXTURE/stub:$PATH"
[ "$(ip_address)" = "192.168.0.166" ]
[ "$(device_hostname)" = "MIYOO282" ]

# Architecture resolution: the armv7 tree uses Dropbear's multi-call binary
# and needs a subcommand, the aarch64 tree uses a plain dropbear.
BIN_DIR="$FIXTURE/bin"
: > "$FIXTURE/bin/arm/dropbearmulti"
: > "$FIXTURE/bin/arm64/dropbear"
chmod +x "$FIXTURE/bin/arm/dropbearmulti" "$FIXTURE/bin/arm64/dropbear"
[ "$(arch_dir)" = arm ] || [ "$(arch_dir)" = arm64 ]
(
	arch_dir() { printf 'arm\n'; }
	[ "$(dropbear_command)" = "$FIXTURE/bin/arm/dropbearmulti dropbear" ]
)
(
	arch_dir() { printf 'arm64\n'; }
	[ "$(dropbear_command)" = "$FIXTURE/bin/arm64/dropbear" ]
)
(
	arch_dir() { printf 'nonexistent\n'; }
	expect_failure dropbear_command
	expect_failure beacon_supported
)

# The passwd bind must be judged from the live mount table, not a marker file
# that outlives a reboot while the mount itself does not.
printf 'rootfs / rootfs rw 0 0\n' > "$FIXTURE/mounts-clean"
printf 'rootfs / rootfs rw 0 0\n/dev/root /etc/passwd ext4 ro 0 0\n' > "$FIXTURE/mounts-bound"
expect_failure env PROC_MOUNTS="$FIXTURE/mounts-clean" sh -c \
	". $DIR/bin/devbridge.sh; passwd_is_bound"
PROC_MOUNTS="$FIXTURE/mounts-bound" sh -c ". $DIR/bin/devbridge.sh; passwd_is_bound"

# Ownership is by content. A bind belonging to another pak must never be
# claimed as ours, or service-off would unmount somebody else's passwd.
cp "$DIR/res/passwd" "$FIXTURE/passwd-ours"
printf 'root:x:0:0:root:/root:/bin/ash\nother:x:1000:1000::/home/other:/bin/sh\n' \
	> "$FIXTURE/passwd-theirs"
PROC_MOUNTS="$FIXTURE/mounts-bound" ETC_PASSWD="$FIXTURE/passwd-ours" \
	PAK_DIR="$DIR" sh -c ". $DIR/bin/devbridge.sh; passwd_is_ours"
expect_failure env PROC_MOUNTS="$FIXTURE/mounts-bound" \
	ETC_PASSWD="$FIXTURE/passwd-theirs" PAK_DIR="$DIR" \
	sh -c ". $DIR/bin/devbridge.sh; passwd_is_ours"
# Matching content but nothing mounted is also not ours.
expect_failure env PROC_MOUNTS="$FIXTURE/mounts-clean" \
	ETC_PASSWD="$FIXTURE/passwd-ours" PAK_DIR="$DIR" \
	sh -c ". $DIR/bin/devbridge.sh; passwd_is_ours"

payload=$(ssh_provider() { printf 'bundled\n'; }; PAK_DIR="$DIR" beacon_payload)
case "$payload" in
	"devbridge host=MIYOO282 ip=192.168.0.166 port=22 battery=64% charge=discharging sftp=yes") ;;
	*) printf 'unexpected beacon payload: %s\n' "$payload" >&2; exit 1 ;;
esac

# Every field must survive a space-separated parse; the charge value is the
# one that would otherwise split into several tokens.
[ "$(printf '%s' "$payload" | tr ' ' '\n' | grep -c '=')" = 6 ]
# charge_token is exercised through a stubbed charge_status: the real read is
# a two-step register protocol whose address write would clobber any fixture.
token_for() {
	FAKE_STATUS="$1"
	( charge_status() { printf '%s\n' "$FAKE_STATUS"; }; charge_token )
}
[ "$(token_for 'Connected (not charging)')" = "connected-not-charging" ]
[ "$(token_for Charging)" = "charging" ]
[ "$(token_for Discharging)" = "discharging" ]
[ "$(token_for '')" = "unknown" ]
[ "$(token_for 'Not charging')" = "Not-charging" ]

beacon_supported
beacon_send "$payload"
grep -q "^239.255.42.99 42099 devbridge host=MIYOO282" "$FIXTURE/stub/udp-log"

# Existing system OpenSSH is adopted only when its configured SFTP subsystem
# is usable. DevBridge never needs to bind a second server on that device.
mkdir -p "$FIXTURE/ssh"
cat > "$FIXTURE/ssh/sshd_config" <<STUB
Subsystem sftp $FIXTURE/ssh/sftp-server
STUB
: > "$FIXTURE/ssh/sftp-server"
chmod +x "$FIXTURE/ssh/sftp-server"
touch "$FIXTURE/stub/port-22-up"
SSHD_CONFIG_GLOB="$FIXTURE/ssh/sshd_config"
export SSHD_CONFIG_GLOB
[ "$(ssh_provider)" = system ]
system_sftp_available
sftp_available
[ "$(openssh_sftp_target)" = "$FIXTURE/ssh/sftp-server" ]
payload=$(beacon_payload)
case "$payload" in *' sftp=yes') ;; *) exit 1 ;; esac

# A configured path that is absent is not advertised as SFTP support.
sed -i "s#sftp-server#missing-sftp-server#" "$FIXTURE/ssh/sshd_config"
expect_failure system_sftp_available
expect_failure sftp_available
unset SSHD_CONFIG_GLOB

# Firmware Dropbear (H700 Base OS) has no sshd_config; its compiled-in SFTP
# server is usable exactly when that file is executable.
touch "$FIXTURE/stub/dropbear-up"
DROPBEAR_SFTP_SERVER="$FIXTURE/ssh/dropbear-sftp-server"
export DROPBEAR_SFTP_SERVER
expect_failure system_sftp_available
: > "$DROPBEAR_SFTP_SERVER"
chmod +x "$DROPBEAR_SFTP_SERVER"
system_sftp_available
sftp_available
rm -f "$FIXTURE/stub/dropbear-up"
expect_failure system_sftp_available
unset DROPBEAR_SFTP_SERVER
rm -f "$FIXTURE/stub/port-22-up"

# service-on adopts a complete system service without mounting passwd or
# launching bundled Dropbear. If system SSH lacks SFTP, port 2222 is the safe
# fallback; an occupied fallback is reported without disturbing either owner.
(
	export SDCARD_PATH="$FIXTURE/sd"
	export PLATFORM=my282
	export USERDATA_PATH="$FIXTURE/sd/.userdata/my282"
	export LOGS_PATH="$USERDATA_PATH/logs"
	export DEVBRIDGE_STATE="$USERDATA_PATH/DevBridge"
	export SSHD_CONFIG_GLOB="$FIXTURE/ssh/sshd_config"
	PATH="$FIXTURE/stub:/usr/bin:/bin"
	sed -i "s#missing-sftp-server#sftp-server#" "$FIXTURE/ssh/sshd_config"
	touch "$FIXTURE/stub/port-22-up"
	"$DIR/bin/service-on"
	grep -q 'using existing system SSH/SFTP on port 22' "$LOGS_PATH/DevBridge.service.txt"
	[ ! -f "$DEVBRIDGE_STATE/dropbear.pid" ]

	sed -i "s#sftp-server#missing-sftp-server#" "$FIXTURE/ssh/sshd_config"
	touch "$FIXTURE/stub/port-2222-up"
	expect_failure "$DIR/bin/service-on"
	grep -q 'fallback port 2222 is occupied' "$LOGS_PATH/DevBridge.service.txt"
	rm -f "$FIXTURE/stub/port-22-up" "$FIXTURE/stub/port-2222-up"
	sed -i "s#missing-sftp-server#sftp-server#" "$FIXTURE/ssh/sshd_config"
)

# The SFTP subsystem request must reach gesftpserver rather than being handed
# to /bin/sh, which is what makes SFTP work without a writable /usr/libexec.
cp "$DIR/bin/shell-wrapper.sh" "$FIXTURE/wrapper/" 2>/dev/null ||
	{ mkdir -p "$FIXTURE/wrapper"; cp "$DIR/bin/shell-wrapper.sh" "$FIXTURE/wrapper/"; }
# Both arch dirs are populated because the wrapper picks one from `uname -m`,
# and the host running these tests is neither armv7 nor aarch64.
for arch in arm arm64; do
	mkdir -p "$FIXTURE/wrapper/$arch"
	cat > "$FIXTURE/wrapper/$arch/gesftpserver" <<'STUB'
#!/bin/sh
echo "gesftpserver ran"
STUB
	chmod +x "$FIXTURE/wrapper/$arch/gesftpserver"
done
chmod +x "$FIXTURE/wrapper/shell-wrapper.sh"
out=$(SSH_ORIGINAL_COMMAND=/usr/libexec/sftp-server "$FIXTURE/wrapper/shell-wrapper.sh")
[ "$out" = "gesftpserver ran" ]
out=$(SSH_ORIGINAL_COMMAND="echo plain command" "$FIXTURE/wrapper/shell-wrapper.sh")
[ "$out" = "plain command" ]

# service-is-running trusts the pid file, not a pgrep by name, so a dropbear
# owned by another pak is not reported as ours.
(
	export SDCARD_PATH="$FIXTURE/sd"
	export PLATFORM=my282
	export USERDATA_PATH="$FIXTURE/sd/.userdata/my282"
	mkdir -p "$USERDATA_PATH/DevBridge"
	expect_failure "$DIR/bin/service-is-running"
	printf '%s\n' 999999 > "$USERDATA_PATH/DevBridge/dropbear.pid"
	expect_failure "$DIR/bin/service-is-running"
	printf '%s\n' "$$" > "$USERDATA_PATH/DevBridge/dropbear.pid"
	# This shell's cmdline does not contain the pak's bin path, so it must
	# still report down. Matching on a binary name instead would be wrong on
	# aarch64, where our dropbear shares its name with the SSH Server pak's.
	expect_failure "$DIR/bin/service-is-running"

	# A live process launched from the pak's own bin directory is recognised,
	# whatever the binary is called. Named "dropbear" precisely because that
	# is the name the SSH Server pak also uses.
	cp "$(command -v sleep)" "$DIR/bin/dropbear"
	"$DIR/bin/dropbear" 30 &
	fake_pid=$!
	printf '%s\n' "$fake_pid" > "$USERDATA_PATH/DevBridge/dropbear.pid"
	"$DIR/bin/service-is-running"
	kill "$fake_pid" 2>/dev/null || true
	rm -f "$DIR/bin/dropbear"
)

# Invoked by a relative path the scripts must still resolve PAK_NAME to
# "DevBridge". A naive dirname walk yields "." and scatters state one level up.
(
	cd "$DIR"
	export SDCARD_PATH="$FIXTURE/sd"
	export PLATFORM=my282
	export USERDATA_PATH="$FIXTURE/sd/.userdata/my282"
	export LOGS_PATH="$USERDATA_PATH/logs"
	rm -rf "$USERDATA_PATH/DevBridge" "$USERDATA_PATH/."/dropbear.pid
	./bin/service-off >/dev/null 2>&1 || true
	[ ! -e "$LOGS_PATH/..service.txt" ]
	[ -f "$LOGS_PATH/DevBridge.service.txt" ]
)

# UI construction and the refresh loop, against a stubbed minui-list.
(
	export DEVBRIDGE_DIR="$DIR"
	export DEVBRIDGE_SOURCE_ONLY=1
	export SDCARD_PATH="$FIXTURE/sd"
	export PLATFORM=my282
	export USERDATA_PATH="$FIXTURE/sd/.userdata/my282"
	export LOGS_PATH="$USERDATA_PATH/logs"
	export DEVBRIDGE_POLL=1
	export POWER_SUPPLY_ROOT NET_ROOT AXP_REG_NODE
	mkdir -p "$FIXTURE/sd/.system/my282/bin"
	cat > "$FIXTURE/sd/.system/my282/bin/governor.sh" <<-EOF
	#!/bin/sh
	printf '%s\n' "\$1" > "$FIXTURE/governor-mode"
	EOF
	chmod +x "$FIXTURE/sd/.system/my282/bin/governor.sh"
	. "$DIR/launch.sh"
	set_cpu_auto
	[ "$(cat "$FIXTURE/governor-mode")" = auto ]

	PATH="$FIXTURE/stub:/usr/bin:/bin"

	menu=$(build_menu)
	jq -e '.items[] | select(.name == "Status:") | .options[0] == "Stopped"' "$menu" >/dev/null
	jq -e '.items[] | select(.name == "Address:") | .options[0] == "192.168.0.166"' "$menu" >/dev/null
	jq -e '.items[] | select(.name == "Battery:") | .options[0] == "64%"' "$menu" >/dev/null
	jq -e '.items[] | select(.name == "Power:") | .options[0] == "Discharging"' "$menu" >/dev/null
	jq -e '.items[] | select(.name == "Start DevBridge") | .features.unselectable == false' "$menu" >/dev/null
	jq -e '.items[] | select(.name == "Discovery beacon: Off")' "$menu" >/dev/null
	# Status rows must never be selectable or the cursor would land on them.
	jq -e '.items[] | select(.name == "Battery:") | .features.unselectable' "$menu" >/dev/null

	# A system provider is displayed as adopted and has no destructive stop
	# action. SFTP is reported from the firmware's configured subsystem.
	touch "$FIXTURE/stub/port-22-up"
	export SSHD_CONFIG_GLOB="$FIXTURE/ssh/sshd_config"
	sed -i "s#missing-sftp-server#sftp-server#" "$FIXTURE/ssh/sshd_config"
	menu=$(build_menu)
	jq -e '.items[] | select(.name == "Status:") | .options[0] == "System SSH"' "$menu" >/dev/null
	jq -e '.items[] | select(.name == "SFTP:") | .options[0] == "sftp://root@192.168.0.166"' "$menu" >/dev/null
	! jq -e '.items[] | select(.name == "Stop DevBridge")' "$menu" >/dev/null
	rm -f "$FIXTURE/stub/port-22-up"
	unset SSHD_CONFIG_GLOB
	menu=$(build_menu)

	# The cursor defaults to the first actionable row, which is the service
	# toggle rather than any of the status rows above it.
	action_index=$(first_action_index "$menu")
	[ "$(jq -r --argjson i "$action_index" '.items[$i].name' "$menu")" = "Start DevBridge" ]

	# A long-lived list is terminated by a state change and reported as a redraw.
	printf '#!/bin/sh\nsleep 30\n' > "$FIXTURE/stub/minui-list"
	chmod +x "$FIXTURE/stub/minui-list"
	( sleep 2; printf '%s\n' 41 > "$FIXTURE/power/battery0/capacity" ) &
	start=$(date +%s)
	rc=0
	main_screen "$menu" >/dev/null || rc=$?
	elapsed=$(( $(date +%s) - start ))
	[ "$rc" -eq 143 ]
	[ "$elapsed" -lt 20 ]
	[ "$(cat "/tmp/${PAK_NAME}-list.rc")" = "143" ]

	# A protocol request cleanly dismisses minui-list and returns a distinct
	# handoff code. It must not be mistaken for an ordinary status redraw.
	printf '#!/bin/sh\nsleep 30\n' > "$FIXTURE/stub/minui-list"
	chmod +x "$FIXTURE/stub/minui-list"
	rm -f "$REQUEST_FILE"
	( sleep 2; printf "'%s'\n" "$DIR/launch.sh" > "$REQUEST_FILE" ) &
	rc=0
	main_screen "$menu" >/dev/null || rc=$?
	[ "$rc" -eq 200 ]
	grep -Fq "$DIR/launch.sh" "$REQUEST_FILE"
	rm -f "$REQUEST_FILE"

	# An unchanged state must NOT redraw: the list stays up until it exits.
	printf '%s\n' 41 > "$FIXTURE/power/battery0/capacity"
	printf '#!/bin/sh\nsleep 4\nexit 2\n' > "$FIXTURE/stub/minui-list"
	rc=0
	main_screen "$menu" >/dev/null || rc=$?
	[ "$rc" -eq 2 ]

	# A confirmed action returns its name and is remembered for the next draw.
	cat > "$FIXTURE/stub/minui-list" <<-'STUB'
	#!/bin/sh
	loc=""
	while [ $# -gt 0 ]; do
		case "$1" in --write-location) loc="$2"; shift ;; esac
		shift
	done
	[ -n "$loc" ] && printf '%s\n' "Start DevBridge" > "$loc"
	exit 0
	STUB
	chmod +x "$FIXTURE/stub/minui-list"
	rc=0
	out=$(main_screen "$menu") || rc=$?
	[ "$rc" -eq 0 ]
	[ "$out" = "Start DevBridge" ]
	remember_selection "$menu" "$out"
	[ "$LAST_SELECTED" = "$(jq -r '[.items[] | .name] | index("Start DevBridge")' "$menu")" ]
)

jq -e '.platforms == ["my282", "tg5040", "my285", "h700"]' "$DIR/pak.json" >/dev/null
jq -e '.launch == "launch.sh"' "$DIR/pak.json" >/dev/null

# Device protocol inventory and launch requests use stable line-oriented
# output, preserve spaces/apostrophes, and never need to signal a frontend.
PROTO_SD="$FIXTURE/protocol-sd"
PROTO_REQUEST="$FIXTURE/protocol-request"
PROTO_SHARED="$FIXTURE/protocol-shared"
mkdir -p "$PROTO_SD/Tools/tg5040/Demo.pak" \
	"$PROTO_SD/Emus/tg5040/GBA.pak" \
	"$PROTO_SD/Emus/tg5040/NDS.pak" \
	"$PROTO_SD/Roms/Game Boy Advance (GBA)" \
	"$PROTO_SD/Roms/Nintendo DS (NDS)" \
	"$PROTO_SHARED/GBA-gambatte"
printf '#!/bin/sh\nexit 0\n' > "$PROTO_SD/Tools/tg5040/Demo.pak/launch.sh"
printf '#!/bin/sh\nEMU_EXE=gambatte\nexit 0\n' > "$PROTO_SD/Emus/tg5040/GBA.pak/launch.sh"
printf '#!/bin/sh\nexit 0\n' > "$PROTO_SD/Emus/tg5040/NDS.pak/launch.sh"
rom="$PROTO_SD/Roms/Game Boy Advance (GBA)/Bob's Test.zip"
: > "$rom"
nds_rom="$PROTO_SD/Roms/Nintendo DS (NDS)/Mario Kart DS.nds"
: > "$nds_rom"
state="$PROTO_SHARED/GBA-gambatte/Bob's Test.zip.st3"
: > "$state"
chmod +x "$PROTO_SD/Tools/tg5040/Demo.pak/launch.sh" \
	"$PROTO_SD/Emus/tg5040/GBA.pak/launch.sh" \
	"$PROTO_SD/Emus/tg5040/NDS.pak/launch.sh"

hello=$(SDCARD_PATH="$PROTO_SD" PLATFORM=tg5040 "$DIR/bin/protocol" hello)
printf '%s\n' "$hello" | grep -q '^DEVBRIDGE/1$'
printf '%s\n' "$hello" | grep -q '^protocol=1$'
[ "$(SDCARD_PATH="$PROTO_SD" PLATFORM=tg5040 "$DIR/bin/protocol" paks | cut -f1)" = Demo ]
[ "$(SDCARD_PATH="$PROTO_SD" PLATFORM=tg5040 "$DIR/bin/protocol" emus | cut -f1)" = \
	"$(printf 'GBA\nNDS')" ]
SDCARD_PATH="$PROTO_SD" PLATFORM=tg5040 "$DIR/bin/protocol" roms GBA |
	grep -Fq "Bob's Test.zip"
state_row=$(SDCARD_PATH="$PROTO_SD" PLATFORM=tg5040 \
	SHARED_USERDATA_PATH="$PROTO_SHARED" "$DIR/bin/protocol" savestates GBA \
	"Game Boy Advance (GBA)/Bob's Test.zip")
[ "$(printf '%s\n' "$state_row" | cut -f1)" = 3 ]
[ "$(printf '%s\n' "$state_row" | cut -f2)" = "$state" ]
SDCARD_PATH="$PROTO_SD" PLATFORM=tg5040 DEVBRIDGE_FOREGROUND=devbridge \
	DEVBRIDGE_REQUEST_FILE="$PROTO_REQUEST" "$DIR/bin/protocol" open-game GBA \
	"Game Boy Advance (GBA)/Bob's Test.zip" >/dev/null
request=$(cat "$PROTO_REQUEST")
eval "set -- $request"
[ "$1" = "$DIR/bin/game-launch" ]
[ "$2" = "$PROTO_SD/Emus/tg5040/GBA.pak/launch.sh" ]
[ "$3" = "$rom" ]
SDCARD_PATH="$PROTO_SD" PLATFORM=tg5040 SHARED_USERDATA_PATH="$PROTO_SHARED" \
	DEVBRIDGE_FOREGROUND=devbridge DEVBRIDGE_REQUEST_FILE="$PROTO_REQUEST" \
	"$DIR/bin/protocol" open-game-state GBA \
	"Game Boy Advance (GBA)/Bob's Test.zip" 3 >/dev/null
request=$(cat "$PROTO_REQUEST")
eval "set -- $request"
[ "$1" = "$DIR/bin/game-launch" ]
[ "$2" = --state ]
[ "$3" = 3 ]
[ "$4" = "$PROTO_SD/Emus/tg5040/GBA.pak/launch.sh" ]
[ "$5" = "$rom" ]
# Plain launches also support standalone emulators that do not declare a
# MinArch EMU_EXE; only their MinArch-specific state inventory is unavailable.
SDCARD_PATH="$PROTO_SD" PLATFORM=tg5040 DEVBRIDGE_FOREGROUND=devbridge \
	DEVBRIDGE_REQUEST_FILE="$PROTO_REQUEST" "$DIR/bin/protocol" open-game NDS \
	"Nintendo DS (NDS)/Mario Kart DS.nds" >/dev/null
request=$(cat "$PROTO_REQUEST")
eval "set -- $request"
[ "$1" = "$DIR/bin/game-launch" ]
[ "$2" = "$PROTO_SD/Emus/tg5040/NDS.pak/launch.sh" ]
[ "$3" = "$nds_rom" ]

# The return wrapper preserves the launched program's status and starts the
# configured DevBridge return launcher inside the current NextUI transaction.
printf '#!/bin/sh\nexit 7\n' > "$FIXTURE/protocol-launcher"
printf '#!/bin/sh\nprintf returned > "$DEVBRIDGE_RETURN_MARKER"\n' > "$FIXTURE/protocol-return"
chmod +x "$FIXTURE/protocol-launcher"
chmod +x "$FIXTURE/protocol-return"
wrapper_rc=0
DEVBRIDGE_RETURN_LAUNCHER="$FIXTURE/protocol-return" \
	DEVBRIDGE_RETURN_MARKER="$FIXTURE/returned" \
	DEVBRIDGE_GAME_LOG="$FIXTURE/game-launch.log" "$DIR/bin/game-launch" \
	"$FIXTURE/protocol-launcher" || wrapper_rc=$?
[ "$wrapper_rc" -eq 7 ]
[ "$(cat "$FIXTURE/game-launch.log" | tail -1)" = "launcher_rc=7" ]
[ "$(cat "$FIXTURE/returned")" = returned ]

# A state launch publishes the requested slot immediately before the emulator,
# then removes an unconsumed resume marker after the emulator returns.
printf '#!/bin/sh\ncat "$DEVBRIDGE_RESUME_SLOT_FILE" > "$DEVBRIDGE_RESUME_CAPTURE"\nexit 7\n' \
	> "$FIXTURE/protocol-state-launcher"
chmod +x "$FIXTURE/protocol-state-launcher"
wrapper_rc=0
DEVBRIDGE_RETURN_LAUNCHER="$FIXTURE/protocol-return" \
	DEVBRIDGE_RETURN_MARKER="$FIXTURE/returned" \
	DEVBRIDGE_RESUME_SLOT_FILE="$FIXTURE/resume-slot" \
	DEVBRIDGE_RESUME_CAPTURE="$FIXTURE/resume-capture" \
	DEVBRIDGE_GAME_LOG="$FIXTURE/game-state-launch.log" "$DIR/bin/game-launch" \
	--state 3 "$FIXTURE/protocol-state-launcher" || wrapper_rc=$?
[ "$wrapper_rc" -eq 7 ]
[ "$(cat "$FIXTURE/resume-capture")" = 3 ]
[ ! -e "$FIXTURE/resume-slot" ]

PYTHONDONTWRITEBYTECODE=1 python3 "$DIR/bin/tests/test-protocol.py"

PYTHONDONTWRITEBYTECODE=1 python3 "$DIR/bin/tests/test-discovery.py"

printf '%s\n' "DevBridge tests passed"
