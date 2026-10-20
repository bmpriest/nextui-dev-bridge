# DevBridge

DevBridge is a development-access and automation tool for the Miyoo A30
(`my282`), TrimUI Brick (`tg5040`), Miyoo Mini Flip (`my285`), and Anbernic
H700 devices (`h700`). It adopts
firmware SSH/SFTP when both are already available, otherwise supplies them
from its bundled Dropbear and `gesftpserver`. It also provides typed remote
commands, virtual controller input, game/save-state launch automation,
screenshots, and functional telemetry over SSH or host-attached ADB. It keeps
the screen awake while it is open, shows connection details and battery state
live, and announces the device on the local network so its address does not
have to be rediscovered after a DHCP lease change.

It expands on the `SSH Server` pak, which manages Dropbear but leaves you to
find the IP yourself, offers no SFTP, and shows no device state.

NextUI launches paks in its maximum-performance profile. DevBridge immediately
returns to the platform's normal menu/Auto CPU profile because its foreground
work is limited to input and periodic status polling (`schedutil` on tg5040).

## The screen

The main screen shows service state, address, ready-to-paste `ssh` and `sftp`
strings, hostname, battery percentage, power state, and discovery state, plus
the time the display last changed.

It refreshes **only when a watched value actually changes** — service state,
address, battery level, power state, boot flag, or beacon flag. The status is
re-read every 3 seconds (`DEVBRIDGE_POLL`), but an unchanged reading redraws
nothing, so the screen can be left open during a transfer without flickering
and without the cursor jumping. `minui-list` has no reload signal, so a redraw
means relaunching it; the cursor returns to the row you last selected.

The rows below the separator act immediately when selected rather than being
edited and saved, so a redraw can never discard a half-made change.

## Keeping the screen on

`minui-list --disable-auto-sleep` calls `PWR_disableAutosleep()` for the
lifetime of that process, which makes `PWR_preventAutosleep()` hold off the
screen-off timer. This works only while the DevBridge screen is in the
foreground, which is exactly the case it is meant for: leave it open while
transferring.

The pak also writes `/tmp/stay_awake`, but only for firmwares that honour that
MinUI-era convention. **NextUI ignores it** — nothing in the NextUI tree reads
that file, so it is not what keeps the screen on here.

## Power reporting

The A30's driver-reported battery `status` is derived from "input present AND
charging enabled" rather than from real current direction, so it claims
`Charging` while the battery drains. DevBridge reads AXP223 register `0x00`
directly and reports:

- `Charging` — charger attached, current reaching the battery
- `Connected (not charging)` — charger attached, **no** current reaching the
  battery; usually a worn USB-C port or a charge-only cable
- `Discharging` — running on battery

If the PMU node is unreadable it falls back to the driver attribute, so the
pak still works on other hardware.

## SFTP

Provider selection is automatic:

- If port 22 is listening and OpenSSH configures an executable SFTP subsystem,
  DevBridge reports `System SSH` and leaves that service entirely alone.
- If nothing owns port 22, bundled Dropbear provides SSH and SFTP there.
- If system SSH owns port 22 but lacks usable SFTP, bundled Dropbear provides
  both services on port 2222. The screen and beacon advertise that port.

Dropbear is built with its SFTP subsystem pointing at `/usr/libexec/sftp-server`,
which does not exist on this firmware. Rather than write to the rootfs,
DevBridge runs Dropbear with a forced command (`-c`) and the wrapper intercepts
the subsystem request, exec'ing the bundled `gesftpserver` instead. This is the
approach spruceOS uses on the same hardware.

Connect with any SFTP client:

```
sftp://root@<address>
```

`scp` also works, since the bundled `dropbearmulti` includes it.

H700 Base OS runs its own Dropbear on port 22 with no config file. Its SFTP
subsystem is the compiled-in `/usr/libexec/sftp-server`, which that firmware
does ship, so DevBridge adopts it as `System SSH` rather than starting a second
server.

## Virtual controller on H700

NextUI's h700 platform reads every `/dev/input/event*` node raw, using the
ANBERNIC-keys codes (A=304, B=305, d-pad on `KEY_UP`..`KEY_RIGHT`), not the
TrimUI SDL layout. On h700, `input-on` starts the virtual pad with
`DEVBRIDGE_INPUT_LAYOUT=h700`, which reports exactly those codes and no axes.
NextUI also opens every pad except the built-in one as an SDL joystick, and
SDL numbers this device's buttons from `KEY_UP` upwards: as a joystick, Up is
button 0, which h700's `JOY_*` table reads as A, so every Up also pressed A. The
h700 virtual pad is therefore named `ANBERNIC-keys`, the one name NextUI-h700
leaves to its evdev path.

## Discovery

With the beacon enabled, the device multicasts a status line to
`239.255.42.99:42099` every 5 seconds.

The transport is the bundled `udpsend` (`src/udpsend.c`, ~5 KB, built by
`make binaries`). It exists because BusyBox on both devices is v1.27.2 built
without `FEATURE_NETCAT_EXTRA`, so `nc` has **no `-u` flag and cannot send UDP
at all**, and neither device ships socat, python or perl. The A30 has no bash
either, ruling out the `/dev/udp` redirection trick.

Multicast is used rather than broadcast so no `SO_BROADCAST` is needed and the
traffic stays on the local link (TTL 1). The current details are also always
written to `DEVBRIDGE-IP.txt` at the root of the SD card, so the address is
recoverable even if multicast is filtered on your network.

Host-side helpers live in `tools/`:

```sh
tools/devbridge-listen.py           # watch beacons as they arrive
tools/devbridge-listen.py --ssh     # print an ssh command and exit
tools/devbridge-find.sh             # fallback: sweep the local /24, ports 22/2222
```

For automation, `--json` emits one JSON object per changed beacon and combines
with `--first` to resolve a device once. The object includes `host`, `ip`,
`port`, `battery`, `charge`, and `sftp`, which lets Netplay test orchestration
select a target without scraping the human display:

```sh
tools/devbridge-listen.py --json --first --timeout 20
tools/devbridge-listen.py --ssh --timeout 20
```

`devbridge-find.sh` needs nothing on the device and also confirms the SSH port
is genuinely open. Set `DEVBRIDGE_PORTS` to override its default `22 2222`.

## Credentials

When DevBridge is the only SSH provider, login is `root` with password `tina`,
provided by bind-mounting the pak's own `res/passwd` over `/etc/passwd` while
the service runs; the firmware file is never modified and the mount is undone
on stop. A system provider keeps its firmware-defined accounts and credentials.
The port-2222 fallback also uses those system accounts so adopting it cannot
change authentication underneath the service already running on port 22.

This is a development service with a well-known password. Do not leave it
enabled on an untrusted network.

## Coexistence

DevBridge tracks its own Dropbear through a pid file rather than a `pgrep` by
name, so it never claims, reconfigures, or kills firmware OpenSSH or a Dropbear
started by another pak. A complete system SSH/SFTP provider is adopted. An SSH
provider without SFTP remains untouched while DevBridge uses port 2222.

## Layout

- `launch.sh` — the live status UI
- `bin/devbridge.sh` — shared helpers (battery, PMU, network, beacon payload)
- `bin/service-on` / `service-off` / `service-is-running` / `on-boot`
- `bin/shell-wrapper.sh` — Dropbear forced command, dispatches SFTP
- `bin/beacon.sh` — multicast announcer
- `bin/protocol` — typed device-side automation endpoint
- `bin/input` / `input-on` — root-only virtual-controller client and daemon
- `bin/game-launch` — normal game/pak launch and return-to-DevBridge lifecycle
- `tools/devbridge.py` — SSH/ADB protocol shell and `.test` runner
- `tools/` — host-side discovery and WSL relay helpers
- `bin/tests/test-devbridge.sh` — host-side test suite (`make test`)

## Bundled binaries

Provenance and licensing for the redistributed binaries are recorded in
`bin/licenses/NOTICE.md`. Note in particular that `gesftpserver` is GPL and was taken
from spruceOS, which ships it without a matching license file — read that
notice before distributing this pak beyond your own devices.

## Platforms

`my282` (Miyoo A30, armv7), `tg5040` (TrimUI Brick, aarch64), and `my285`
(Miyoo Mini Flip, arm). Architecture binaries are selected from `uname -m` at
runtime; platform-specific `minui-list` and `jq` builds live under `bin/`.

Host key handling differs between them, because `-R` does **not** create the
file named by `-r` — verified on the A30, where dropbear started, generated
nothing, and closed every connection. The armv7 build is Dropbear's multi-call
binary, so the key is generated up front with `dropbearkey` and kept on the SD
card. The aarch64 build is a plain `dropbear` with no `dropbearkey`, so it
falls back to `-R` writing into `/etc/dropbear`.

## Status

**Bundled provider validated on hardware, both devices** (2026-08-11).

TrimUI Brick (`tg5040`, aarch64) and Miyoo A30 (`my282`, armv7): service
start/stop, correct state paths, service detection, SSH login landing in
`/mnt/SDCARD` as root, the SFTP subsystem (a real `SSH_FXP_INIT` returns
`SSH_FXP_VERSION` v3 through the wrapper into `gesftpserver`), `udpsend`
transmitting, the status helpers, and a clean stop that leaves the existing
SSH server on port 22 untouched.

On the A30 the PMU path was confirmed against a live fault: register `0x00`
read `0xc0` and the pak reported `Connected (not charging)` while the driver
attribute claimed `Charging`. The passwd ownership rule was confirmed on the
same device — another pak held the `/etc/passwd` bind, DevBridge declined to
claim it, and stopping DevBridge left that mount intact.

The TrimUI 1.1.1 system OpenSSH/SFTP provider was verified on a Brick on
2026-08-23. Automatic adoption is covered by the host-side tests.

DevBridge 0.7.0 has ten passing host tests. On 2026-08-28, Brick `.144` was
used to validate protocol inventory, a single persistent virtual controller,
framebuffer capture, functional telemetry, game launch, normal MinArch menu
quit, and automatic return to DevBridge without signalling NextUI. A full
Tetris launch/capture/quit/return cycle completed unattended. Save-state
inventory and launch were then validated with the existing 59,629-byte Tetris
DX slot-9 state; MinArch consumed the resume marker without logging a state
open/read/unserialize failure and returned normally.

ADB is implemented as a host-runner transport (`adb shell` and `adb pull`) and
covered by unit tests. SSH automation is fully live-validated; ADB requires a 
suitable udev rule or a privileged ADB server before the same validation can run.

For WSL2 NAT, `tools/devbridge-relay-wsl.py` is the deterministic path. Run it
with Windows Python so it receives LAN multicast and forwards only validated
DevBridge messages to the current WSL address as UDP unicast:

```powershell
py tools\devbridge-relay-wsl.py
# Add --distribution Ubuntu when the default WSL distro is not the target.
```

Then receive inside WSL without joining a multicast group:

```sh
python3 tools/devbridge-listen.py --unicast
python3 tools/devbridge-listen.py --unicast --json --first --timeout 20
```

The relay refreshes the WSL address every 15 seconds because NAT guest
addresses can change across restarts. `--interface` selects a specific Windows
LAN IPv4 address, `--target` bypasses WSL address discovery for diagnostics,
and `--quiet` suppresses per-packet forwarding output. If no beacon arrives for
15 seconds, the relay closes and rejoins the multicast group; this recovers the
Windows socket after Wi-Fi/Ethernet disconnects and adapter route changes.
`--rejoin-after` and `--retry` tune that watchdog.

## Automated button input

When the kernel provides `/dev/uinput`, DevBridge starts a persistent virtual
controller alongside its access service. Commands sent through SSH or ADB
become ordinary Linux input events, so `minui-list`, MinArch, and SDL-based
emulators see the same joystick buttons they see from the built-in controls:

```sh
ssh root@<brick-ip> '/mnt/SDCARD/Tools/tg5040/DevBridge.pak/bin/input tap a'
adb -s <serial> shell '/mnt/SDCARD/Tools/tg5040/DevBridge.pak/bin/input tap down 150'
```

Buttons are `up`, `down`, `left`, `right`, `a`, `b`, `x`, `y`, `l1`, `r1`,
`l2`, `r2`, `l3`, `r3`, `select`, `start`, and `menu`. `tap` defaults to 80 ms
and accepts a duration from 1–10000 ms. `release-all` provides test cleanup,
and `status` checks that the daemon is ready. The control socket is root-only
and is not exposed as another network service; SSH or host-attached ADB remains
the transport boundary.

For multi-step and unattended work, `tools/devbridge.py` provides an
interactive protocol shell and a `.test` runner over persistent SSH and/or ADB
connections:

```sh
python3 tools/devbridge.py --connect 192.168.0.144:brick shell
python3 tools/devbridge.py --connect 192.168.0.144:brick run example.test
python3 tools/devbridge.py --adb BRICK_SERIAL=brick shell
```

It can inventory ROMs/emulators/paks and their associated MinArch save states,
operate buttons and chords, launch games normally or directly into a verified
state slot, return to DevBridge after MinArch quits, capture the visible
framebuffer, sample performance into CSV, run explicit terminal escape
commands, and retain a JSONL transcript. Devices may use persistent SSH/SCP or
USB ADB (`adb shell`/`adb pull`), including both transports in one session.

The current `PERF` command provides functional diagnostic telemetry, not a
calibrated benchmark. Each sample invokes remote commands and reads `/proc` and
sysfs, so its observer overhead has not yet been quantified. Use it to compare
coarse behavior, and use the planned buffered on-device sampler for performance
claims where a few percentage points matter.

See `PROTOCOL.md` for the command grammar, safety boundary, artifacts, and
current v1 limitations.
