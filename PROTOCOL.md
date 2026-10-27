# DevBridge protocol v1

DevBridge provides a line-oriented host protocol for unattended device work.
The host process owns persistent SSH connections and/or selected ADB devices;
the device exposes a small typed endpoint at `bin/protocol`. DevBridge adds no
protocol listener: commands travel through authenticated SSH or a host-attached
ADB transport.

## Host entry points

From the DevBridge pak source directory:

```sh
python3 tools/devbridge.py --connect 192.168.0.144:brick shell
python3 tools/devbridge.py --connect 192.168.0.144:brick run example.test
python3 tools/devbridge.py --connect 192.168.0.144:brick exec STATUS
python3 tools/devbridge.py --adb BRICK_SERIAL=brick shell
```

The bundled-provider password defaults to DevBridge's documented development
password, `tina`. Set `DEVBRIDGE_PASSWORD` for another password, or pass
`--identity PATH` for key authentication. With `--identity PATH
--password-fallback`, devices that accept the key use it and the rest fall
back to the password, so one run can mix devices set up either way.

`--connect ADDRESS[:ALIAS[:PORT]]` takes an optional per-device SSH port, for a
device reached through a tunnel (`127.0.0.1:guest:40123`) alongside direct
ones; `--port` remains the default for the others.

The intended agent approval boundary is the stable command prefix:

```text
python3 tools/devbridge.py
```

One approved invocation can run for hours. SSH devices use reusable OpenSSH
control sockets; ADB devices use `adb shell` and `adb pull`. Both transports
remain inside the runner. This does not bypass the host agent's permission
system: the user explicitly approves the runner, whose scope includes `TRM`
and is therefore equivalent to root shell access on connected development
devices.

## Test language

Files are UTF-8, one command per line. Blank lines and `#` comments are
ignored. Arguments use shell-style quoting, but no local shell expansion is
performed. Prefix a command with `alias:` or `all:` to choose its target.
Protocol command starts are paced per device by 100 ms by default. Override
this with `--command-delay-ms`; UI transitions should still use `WAIT_FOR` or
an explicit `WAIT` instead of relying on this transport-level pacing.

### Session and connections

```text
SESSION NAME [OUTPUT_ROOT]
DISCOVER [TIMEOUT_SECONDS]
CONNECT ADDRESS [ALIAS]
CONNECT_ADB SERIAL [ALIAS]
ALIAS OLD NEW
ALIAS_CMD NAME COMMAND...
STATUS
```

`SESSION` creates a directory containing `events.jsonl`, screenshots, and
performance CSV files. Names accept `strftime` directives plus `{date}` and
`{time}`. Without `SESSION`, the runner creates a timestamped session lazily.

`CONNECT_ADB` selects a device by the serial printed by `adb devices`. The CLI
form is `--adb SERIAL[=ALIAS]`. SSH and ADB devices can coexist in one session;
aliases and `all:` targeting work identically for both. Screenshot retrieval
uses `adb pull` for ADB devices instead of SCP. USB permissions and attachment
remain host/WSL responsibilities rather than device-protocol operations.
The ADB transport is live-validated: Netplay's unattended test matrix
(2026-10-26/27) drove a Brick over USB ADB alongside SSH devices for over a
hundred cells. An ADB device keeps working when its WiFi changes networks,
which makes it the stable anchor of a multi-device rig.

### Inventory and terminal access

```text
GET_BTNS
GET_PAKS
GET_EMUS
GET_ROM_LIST
GET_ROM_BY_SYSTEM TAG
GET_ROM_SAVESTATES TAG ROM
TRM COMMAND...
```

Inventory output is tab-separated where each row has multiple fields. `TRM`
is the deliberate escape hatch for settings, log inspection, deployment, and
diagnostics that do not yet have typed commands.

`GET_ROM_SAVESTATES` resolves the active MinArch core for the emulator tag and
lists only states associated with that ROM/core. Rows contain slot, absolute
path, byte size, and modification epoch. Slot `9` is MinArch's automatic or
hidden resume slot and may also be written as `auto` when launching.

### Input

```text
BTN A
BTN HOLD LEFT 500
BTN PRESS L1,R1
BTN RELEASE L1,R1
BTN REPEAT A DURATION 5000 FREQUENCY 250
BTN STOP
```

Durations and frequencies are milliseconds. Chords press in listed order and
release in reverse order. Scripted repeats are finite; this prevents an
unattended test from leaving a permanent repeat behind.

### Launch lifecycle

```text
OPEN_GAME TAG ROM
OPEN_GAME_STATE TAG ROM SLOT
OPEN_RANDOM_GAME TAG
OPEN_PAK NAME_OR_PATH
QUIT_GAME
WAIT_FOR game|devbridge [TIMEOUT_MS]
```

The device must be displaying DevBridge before `OPEN_GAME`, `OPEN_GAME_STATE`,
or `OPEN_PAK`.
DevBridge consumes the request by replacing its UI process with the validated
wrapper inside NextUI's existing launch transaction. The wrapper launches the
selected emulator/pak, then runs DevBridge again in that transaction when it
returns. `QUIT_GAME` operates MinArch's five-row menu using controller input,
so saves and frontend cleanup follow the same path as physical input. It does
not send Unix signals or terminate NextUI.

ROMs may be relative to `Roms/`, absolute beneath `/mnt/SDCARD/Roms`, or use
the inventory form `TAG://relative/path`. Emulator tags resolve to
`Emus/PLATFORM/TAG.pak/launch.sh`.

DraStic (NDS.pak) enumerates only the first SDL joystick, so for an NDS.pak
launch the wrapper bind-mounts the virtual pad over the physical pad's
`/dev/input` nodes and mirrors the physical buttons into it, undoing both when
DraStic exits. The undo removes every bind stacked on those nodes, lazily if a
process still holds them, and stale binds are cleared before binding: a silently
failed `umount` used to leave one bind per launch, and the next launch's mirror
then copied the virtual pad into itself, a flood of D-pad events that scrolled
DraStic's menus on their own. The mirror also refuses a source that is its own
destination.

`OPEN_GAME_STATE` first verifies that the requested slot exists for the ROM
and active core. The wrapper atomically publishes MinArch's ordinary
`/tmp/resume_slot.txt` immediately before launch. MinArch consumes that marker
after loading the game, so state loading follows the same frontend path as a
NextUI resume. An unconsumed marker is removed when the launcher returns.

### Observation

```text
SCREEN [LABEL]
VERIFY REFERENCE [threshold=N] [region=X,Y,W,H]
VERIFY_WAIT REFERENCE TIMEOUT_MS [threshold=N] [region=X,Y,W,H]
VERIFY_UNTIL REFERENCE TIMEOUT_MS [every=MS] [threshold=N] [region=X,Y,W,H] :: COMMAND...
PERF [DURATION_MS] [INTERVAL_MS]
WAIT MILLISECONDS
KEEP_SCREEN_ON
```

`VERIFY` compares the visible screen with a reference PNG once and fails the
run if it does not match. The score is the mean absolute difference of a 64x48
greyscale reduction: 0 is identical, the same screen with changed text scores a
few units, a different screen tens. The default threshold is 10. `region=`
crops both images first; integers are pixels, values with a decimal point are
fractions of the screen, so one crop works across resolutions.

`VERIFY_WAIT` keeps capturing, at most every 0.5 s, until the screen matches or
the timeout runs out, and returns on the first match. (Before ee0079b it looked
only at the start, middle and end of the timeout, which made test timing a
guess.) Because it returns at the first match, a generous timeout costs nothing
when the screen is on time: size it for the slowest plausible case. It also
means a following press can arrive the moment a screen appears, before the game
accepts input; settle with `WAIT` or use `VERIFY_UNTIL`.

`VERIFY_UNTIL` is for a press whose effect lands late or can be lost: it checks
for the screen for `every` ms (default 3000) and, while it is not there, runs
`COMMAND` on the device through the terminal endpoint (typically a key-press
helper), then checks again, until `TIMEOUT_MS`. The check always comes first,
so nothing is pressed once the screen shows. Use it only where a repeated press
is harmless once the target screen is up.

Every failing check keeps its closest capture as
`verify-miss-REFERENCE-ALIAS.png` in the session directory.

`SCREEN` captures only the visible framebuffer page and converts BGRA to PNG
when host `ffmpeg` is available. `PERF` records timestamp, foreground state,
load, available memory, CPU frequency/temperature, game PID, CPU ticks, and
RSS into CSV. The JSONL transcript records commands, outputs, timing, errors,
and artifact paths.

`PERF` is functional telemetry, not a calibrated benchmark facility. Each
sample currently starts a remote command and reads `/proc`/sysfs, so its
observer effect has not been bounded. Do not use it to support small
performance comparisons. The planned buffered `PERF_START`/`PERF_STOP`
sampler will collect locally without SSH or file transfer during the measured
window and will be calibrated against an instrumentation-disabled baseline.

`KEEP_SCREEN_ON` records the MinUI-compatible flag; while DevBridge itself is
visible, its UI also holds NextUI's actual autosleep inhibitor. A future
frontend IPC revision is still needed to guarantee no autosleep during a
completely input-idle game without synthesizing gameplay input.

## Device endpoint

The stable device greeting is:

```text
DEVBRIDGE/1
protocol=1
platform=tg5040
input=yes
```

Device commands currently include `hello`, `status`, `buttons`, `paks`,
`emus`, `roms`, `savestates`, `input`, `open-game`, `open-game-state`,
`open-pak`, `screen`, `perf`, `keep-screen-on`, and `terminal`. Success and
metadata use `key=value` lines; inventories use TSV; failures are nonzero with
`error=...` on stderr.

## Current validation status

DevBridge 0.7.0's host-side tests pass (`make test`). Netplay's unattended
matrix (2026-10-26/27) exercised SSH (key and password), ADB and tunnelled
devices together, `VERIFY_WAIT`/`VERIFY_UNTIL`, and DraStic, DSperate and
MinArch launches on TrimUI Brick and RG35XXH for about ten hours. On TrimUI Brick `.144`, the
protocol has been validated over SSH for inventory, controller input,
framebuffer capture, telemetry capture, game launch, normal MinArch quit, and
automatic return to DevBridge. `GET_ROM_SAVESTATES` and `OPEN_GAME_STATE` were
validated with the existing 59,629-byte Tetris DX slot-9 state; MinArch consumed
the resume marker without a state open/read/unserialize error and returned
normally. Input-daemon platform inference, duplicate-controller cleanup, and
Unix-socket ownership were also exercised on hardware.

ADB command execution and `adb pull` are implemented and unit-tested but are
not yet live-validated from this agent environment because its Linux ADB
server reports insufficient USB permissions for both attached Bricks.

## Deliberate v1 limits

- Conditional `IF/THEN/ELSE` syntax is reserved but not implemented yet.
- `QUIT_GAME` knows MinArch's standard five-row menu and drives it with
  remote presses. Over a slow or tunnelled link those presses stretch into
  auto-repeats, and an older MinArch may lay its menu out differently; tests
  that must exit reliably run an on-device quit helper with fallbacks
  (Netplay's `testing/devbridge/device/quit.sh`). Other standalone emulators
  need typed exit adapters.
- Remote `BTN` presses are two round trips each (press, release), so their
  hold time includes link latency. Timing-sensitive input should run on the
  device (a helper invoked with `TRM`) rather than as `BTN`.
- There is no suite-level setup or teardown yet: a test inherits whatever the
  previous one left (settings, emulator configuration, processes). See the
  planned `CONFIG_SNAPSHOT`/`CONFIG_RESTORE`, `FINALLY`,
  `RETURN_TO_DEVBRIDGE` and `RECOVER_INPUT`.
- Settings currently use `TRM`; typed transactional config edits and automatic
  restore/rollback belong in the next protocol revision.
- Performance sampling observes Linux/frontend telemetry. Emulator-specific
  frame-time and core counters require adapters that parse or expose their
  structured logs.

## Planned additions, in implementation order

1. `HASH`, `PUSH`, `PULL`, and `VERIFY` for atomic, hash-verified transfer.
2. `ASSERT` plus generalized `WAIT_FOR` conditions for files, processes,
   ports, logs, foreground state, and performance thresholds.
3. `DEPLOY_PAK` and `ROLLBACK_PAK` for staged installs with manifests,
   backups, validation, and automatic recovery.
4. `COLLECT` for typed log, configuration, crash, and system-state bundles.
5. `CONFIG_SNAPSHOT`, `CONFIG_GET`, `CONFIG_SET`, and `CONFIG_RESTORE` for
   transactional benchmark settings.
6. `SET`, `CAPTURE`, interpolation, bounded `REPEAT`, `IF/ELSE`, and `INCLUDE`
   for deterministic test composition.
7. `FINALLY`, `RETURN_TO_DEVBRIDGE`, `RECONNECT`, and `RECOVER_INPUT` for
   guaranteed cleanup and unattended recovery.
8. `PERF_START`, `PERF_MARK`, `PERF_STOP`, and `PERF_FETCH`, using a quiet,
   buffered device sampler instead of SSH polling during benchmark windows.
9. `PARALLEL`, `BARRIER`, and `SYNC_BTN` for coordinated multi-device tests.
10. `READ_MENU`, `WAIT_FOR MENU`, and semantic menu selection. Frontends will
    publish state only when a menu opens or changes, keeping gameplay overhead
    at zero; screenshot OCR will not be part of the authoritative path.
