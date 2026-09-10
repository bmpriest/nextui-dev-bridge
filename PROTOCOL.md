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
`--identity PATH` for key authentication.

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
The ADB transport is implemented and covered by host tests. On the current WSL
agent host, both Bricks enumerate as USB ID `18d1:d002`, but live commands are
still blocked by Linux USB permissions until an appropriate udev rule or a
privileged ADB server is configured.

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

`OPEN_GAME_STATE` first verifies that the requested slot exists for the ROM
and active core. The wrapper atomically publishes MinArch's ordinary
`/tmp/resume_slot.txt` immediately before launch. MinArch consumes that marker
after loading the game, so state loading follows the same frontend path as a
NextUI resume. An unconsumed marker is removed when the launcher returns.

### Observation

```text
SCREEN [LABEL]
PERF [DURATION_MS] [INTERVAL_MS]
WAIT MILLISECONDS
KEEP_SCREEN_ON
```

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

DevBridge 0.7.0 has ten passing host-side tests. On TrimUI Brick `.144`, the
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
- `QUIT_GAME` knows MinArch's standard five-row menu. Other standalone
  emulators need typed exit adapters.
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
