#!/usr/bin/env python3
"""DevBridge protocol REPL and .test runner.

One invocation owns SSH or USB ADB connections for every connected device, so
an agent or human can approve the stable host command once and then run a long
test without approving each underlying transport operation.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
from pathlib import Path
import random
import shlex
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time


BEACON_GROUP = "239.255.42.99"
BEACON_PORT = 42099
DEFAULT_PASSWORD = "tina"
OUTPUT_MARKER = "__DEVBRIDGE_OUTPUT_V1__"


class ProtocolError(RuntimeError):
    pass


def verify_options(extras):
    """Parse the trailing `key=value` options of VERIFY / VERIFY_WAIT.

    Kept as named options rather than positional arguments so the common case
    stays a single reference path, while a check that needs to look at one part
    of the screen can say so. Comparing whole screens cannot, for instance,
    tell a lobby with one player from one with two: the difference is a single
    row of text and it disappears into the average."""
    options = {}
    for extra in extras:
        if "=" not in extra:
            raise ProtocolError(f"VERIFY: expected key=value, got {extra!r}")
        key, _, value = extra.partition("=")
        key = key.strip().lower()
        if key == "threshold":
            options["threshold"] = float(value)
        elif key == "region":
            parts = [int(piece) for piece in value.split(",")]
            if len(parts) != 4:
                raise ProtocolError("VERIFY: region must be X,Y,W,H")
            options["region"] = tuple(parts)
        else:
            raise ProtocolError(f"VERIFY: unknown option {key!r}")
    return options


def key_values(text: str) -> dict[str, str]:
    result = {}
    for line in text.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            result[key] = value
    return result


def has_protocol_marker(text: str) -> bool:
    """Accept an exact marker line even when the SSH server prints a banner."""
    return "DEVBRIDGE/1" in text.splitlines()


def marked_output(text: str) -> str:
    """Discard SSH login banners printed before the remote command output."""
    marker = OUTPUT_MARKER + "\n"
    _before, separator, after = text.partition(marker)
    return after if separator else text


class SSHTransport:
    def __init__(self, args):
        self.args = args
        self.temp = tempfile.TemporaryDirectory(prefix="devbridge-")
        root = Path(self.temp.name)
        self.askpass = root / "askpass.sh"
        password = os.environ.get(args.password_env, DEFAULT_PASSWORD)
        self.askpass.write_text("#!/bin/sh\nprintf '%s\\n' " + shlex.quote(password) + "\n")
        self.askpass.chmod(0o700)
        self.config = root / "ssh_config"
        lines = [
            "Host *",
            f"    ConnectTimeout {args.connect_timeout}",
            "    ConnectionAttempts 2",
            "    ServerAliveInterval 5",
            "    ServerAliveCountMax 3",
            "    StrictHostKeyChecking no",
            "    UserKnownHostsFile /dev/null",
            "    LogLevel ERROR",
            "    ControlMaster auto",
            f'    ControlPath "{root}/ssh-%C"',
            "    ControlPersist 120",
        ]
        if args.identity:
            lines += [f'    IdentityFile "{args.identity}"', "    BatchMode yes"]
        else:
            lines += ["    PreferredAuthentications password", "    PubkeyAuthentication no"]
        self.config.write_text("\n".join(lines) + "\n")
        self.config.chmod(0o600)

    def close(self):
        self.temp.cleanup()

    def environment(self):
        env = os.environ.copy()
        env.update(
            DISPLAY=env.get("DISPLAY", ":0"),
            SSH_ASKPASS=str(self.askpass),
            SSH_ASKPASS_REQUIRE="force",
        )
        return env

    def options(self, port: int, *, scp=False):
        return ["-F", str(self.config), "-P" if scp else "-p", str(port)]

    def run(self, device, command: str, *, check=True, timeout=None):
        if device.transport == "adb":
            argv = ["adb", "-s", device.address, "shell", f"sh -c {shlex.quote(command)}"]
            environment = os.environ.copy()
            start_new_session = False
            transport_name = "ADB"
        else:
            argv = [
                "ssh", *self.options(device.port),
                f"{self.args.user}@{device.address}",
                "sh", "-lc", shlex.quote(command),
            ]
            environment = self.environment()
            start_new_session = not bool(self.args.identity)
            transport_name = "SSH"
        try:
            result = subprocess.run(
                argv, text=True, capture_output=True, stdin=subprocess.DEVNULL,
                env=environment, start_new_session=start_new_session,
                timeout=timeout or self.args.operation_timeout,
            )
        except subprocess.TimeoutExpired as error:
            raise ProtocolError(f"{device.alias}: {transport_name} command timed out") from error
        except FileNotFoundError as error:
            raise ProtocolError(f"{transport_name.lower()} executable not found") from error
        if check and result.returncode:
            detail = result.stderr.strip() or result.stdout.strip()
            raise ProtocolError(f"{device.alias}: remote command failed: {detail}")
        return result

    def download(self, device, remote_path: str, local_path: Path):
        if device.transport == "adb":
            argv = ["adb", "-s", device.address, "pull", remote_path, str(local_path)]
            environment = os.environ.copy()
            start_new_session = False
        else:
            argv = [
                "scp", *self.options(device.port, scp=True),
                f"{self.args.user}@{device.address}:{remote_path}", str(local_path),
            ]
            environment = self.environment()
            start_new_session = not bool(self.args.identity)
        try:
            result = subprocess.run(
                argv, text=True, capture_output=True, stdin=subprocess.DEVNULL,
                env=environment, start_new_session=start_new_session,
                timeout=self.args.operation_timeout,
            )
        except FileNotFoundError as error:
            name = "adb" if device.transport == "adb" else "scp"
            raise ProtocolError(f"{name} executable not found") from error
        if result.returncode:
            raise ProtocolError(f"{device.alias}: download failed: {result.stderr.strip()}")


class Device:
    def __init__(self, address: str, alias: str, port: int | None, platform: str,
                 transport: str = "ssh"):
        self.address = address
        self.alias = alias
        self.port = port
        self.platform = platform
        self.transport = transport

    @property
    def endpoint(self):
        return f"adb:{self.address}" if self.transport == "adb" else f"{self.address}:{self.port}"

    @property
    def protocol_path(self):
        return f"/mnt/SDCARD/Tools/{self.platform}/DevBridge.pak/bin/protocol"


class Runner:
    def __init__(self, args):
        self.args = args
        self.transport = SSHTransport(args)
        self.devices: dict[str, Device] = {}
        self.named_commands: dict[str, str] = {}
        self.session_dir: Path | None = None
        self.events = None
        self.command_number = 0
        self.last_protocol_start: dict[str, float] = {}

    def close(self):
        if self.events:
            self.events.close()
        self.transport.close()

    def ensure_session(self):
        if self.session_dir is None:
            stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            self.start_session(f"devbridge-{stamp}", self.args.session_root)

    def start_session(self, name: str, root: str | Path):
        now = dt.datetime.now()
        name = now.strftime(name).replace("{date}", now.strftime("%Y%m%d")).replace(
            "{time}", now.strftime("%H%M%S")
        )
        path = Path(root).expanduser() / name
        path.mkdir(parents=True, exist_ok=True)
        if self.events:
            self.events.close()
        self.session_dir = path.resolve()
        self.events = (self.session_dir / "events.jsonl").open("a", encoding="utf-8")
        print(f"session: {self.session_dir}")

    def record(self, event: str, **fields):
        self.ensure_session()
        payload = {
            "time": dt.datetime.now(dt.timezone.utc).isoformat(),
            "event": event,
            **fields,
        }
        self.events.write(json.dumps(payload, sort_keys=True) + "\n")
        self.events.flush()

    def protocol(self, device: Device, command: str, *arguments, check=True):
        delay = self.args.command_delay_ms / 1000
        last_start = self.last_protocol_start.get(device.alias)
        if last_start is not None:
            remaining = delay - (time.monotonic() - last_start)
            if remaining > 0:
                time.sleep(remaining)
        self.last_protocol_start[device.alias] = time.monotonic()
        endpoint = " ".join(
            [shlex.quote(device.protocol_path), shlex.quote(command)]
            + [shlex.quote(str(argument)) for argument in arguments]
        )
        remote = f"printf '{OUTPUT_MARKER}\\n'; {endpoint}"
        started = time.monotonic()
        result = self.transport.run(device, remote, check=check)
        result.stdout = marked_output(result.stdout)
        self.record(
            "remote",
            device=device.alias,
            address=device.address,
            transport=device.transport,
            command=command,
            arguments=list(arguments),
            returncode=result.returncode,
            elapsed_ms=round((time.monotonic() - started) * 1000),
            stdout=result.stdout,
            stderr=result.stderr,
        )
        return result

    def connect(self, address: str, alias: str | None = None, port: int | None = None):
        port = port or self.args.port
        alias = alias or address.replace(".", "-")
        if alias in self.devices:
            raise ProtocolError(f"alias already connected: {alias}")
        device = Device(address, alias, port, self.args.platform)
        result = self.protocol(device, "hello")
        if not has_protocol_marker(result.stdout):
            raise ProtocolError(f"{alias}: incompatible or missing DevBridge protocol")
        hello = key_values(result.stdout)
        device.platform = hello.get("platform", device.platform)
        self.devices[alias] = device
        print(f"connected {alias}={address}:{port} platform={device.platform}")

    def connect_adb(self, serial: str, alias: str | None = None):
        alias = alias or f"adb-{serial}"
        if alias in self.devices:
            raise ProtocolError(f"alias already connected: {alias}")
        device = Device(serial, alias, None, self.args.platform, transport="adb")
        result = self.protocol(device, "hello")
        if not has_protocol_marker(result.stdout):
            raise ProtocolError(f"{alias}: incompatible or missing DevBridge protocol over ADB")
        hello = key_values(result.stdout)
        device.platform = hello.get("platform", device.platform)
        self.devices[alias] = device
        print(f"connected {alias}=adb:{serial} platform={device.platform}")

    def selected(self, prefix: str | None):
        if prefix == "all":
            if not self.devices:
                raise ProtocolError("no connected devices")
            return list(self.devices.values())
        if prefix:
            if prefix not in self.devices:
                raise ProtocolError(f"unknown device alias: {prefix}")
            return [self.devices[prefix]]
        if len(self.devices) != 1:
            raise ProtocolError("command needs a device alias when connection count is not one")
        return list(self.devices.values())

    def discover(self, timeout=5.0):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", BEACON_PORT))
        if not self.args.unicast:
            membership = struct.pack(
                "=4s4s", socket.inet_aton(BEACON_GROUP), socket.inet_aton(self.args.interface)
            )
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
        sock.settimeout(timeout)
        deadline = time.monotonic() + timeout
        found = {}
        while time.monotonic() < deadline:
            try:
                data, sender = sock.recvfrom(4096)
            except socket.timeout:
                break
            text = data.decode("utf-8", "replace").strip()
            if not text.startswith("devbridge "):
                continue
            fields = key_values(text.replace(" ", "\n"))
            address = fields.get("ip", sender[0])
            found[address] = fields
        sock.close()
        for address, fields in sorted(found.items()):
            print(f"{address}:{fields.get('port', '22')} {fields.get('host', '?')} "
                  f"battery={fields.get('battery', '?')}")
        self.record("discover", devices=found)
        return found

    def status(self, devices):
        for device in devices:
            result = self.protocol(device, "status", check=False)
            values = key_values(result.stdout)
            state = " ".join(f"{key}={value}" for key, value in values.items())
            print(f"{device.alias}: {device.endpoint} {state}")

    def input_action(self, devices, arguments):
        if not arguments:
            raise ProtocolError("BTN requires a button or action")
        action = arguments[0].lower()
        if action not in {"press", "hold", "release", "repeat", "stop"}:
            action = "tap"
            button_text = arguments[0]
            rest = arguments[1:]
        else:
            button_text = arguments[1] if len(arguments) > 1 else ""
            rest = arguments[2:]
        if action == "stop":
            for device in devices:
                self.protocol(device, "input", "release-all")
            return
        buttons = [button.strip().lower() for button in button_text.split(",") if button.strip()]
        if not buttons:
            raise ProtocolError("BTN requires at least one button")
        duration = 80 if action == "tap" else 500
        frequency = 500
        index = 0
        while index < len(rest):
            token = rest[index].lower()
            if token == "duration" and index + 1 < len(rest):
                duration = int(rest[index + 1]); index += 2
            elif token == "frequency" and index + 1 < len(rest):
                frequency = int(rest[index + 1]); index += 2
            elif token.isdigit():
                duration = int(token); index += 1
            else:
                raise ProtocolError(f"unknown BTN option: {rest[index]}")
        if action == "release":
            for device in devices:
                for button in reversed(buttons):
                    self.protocol(device, "input", "release", button)
            return
        if action == "press":
            for device in devices:
                for button in buttons:
                    self.protocol(device, "input", "press", button)
            return

        def chord_once(device, held_ms):
            for button in buttons:
                self.protocol(device, "input", "press", button)
            time.sleep(held_ms / 1000)
            for button in reversed(buttons):
                self.protocol(device, "input", "release", button)

        if action in {"tap", "hold"}:
            for device in devices:
                chord_once(device, duration)
        else:
            deadline = time.monotonic() + duration / 1000
            while time.monotonic() < deadline:
                for device in devices:
                    chord_once(device, min(80, frequency))
                remaining = frequency / 1000 - min(80, frequency) / 1000
                if remaining > 0:
                    time.sleep(remaining)

    def wait_for(self, devices, expected: str, timeout_ms: int):
        deadline = time.monotonic() + timeout_ms / 1000
        pending = {device.alias: device for device in devices}
        while pending and time.monotonic() < deadline:
            for alias, device in list(pending.items()):
                result = self.protocol(device, "status", check=False)
                if key_values(result.stdout).get("foreground") == expected:
                    del pending[alias]
            if pending:
                time.sleep(0.5)
        if pending:
            raise ProtocolError(f"timed out waiting for {expected}: {', '.join(pending)}")

    def open_game(self, devices, tag: str, rom: str, state: str | None = None):
        for device in devices:
            command = "open-game-state" if state is not None else "open-game"
            arguments = (tag, rom, state) if state is not None else (tag, rom)
            result = self.protocol(device, command, *arguments)
            print(f"{device.alias}: {result.stdout.strip()}")
        self.wait_for(devices, "game", self.args.launch_timeout * 1000)

    def quit_game(self, devices):
        # Current MinArch's main menu has Continue, Save, Load, Options, Quit.
        self.input_action(devices, ["menu", "120"])
        time.sleep(0.5)
        for _ in range(4):
            self.input_action(devices, ["down", "80"])
        self.input_action(devices, ["a", "120"])
        self.wait_for(devices, "devbridge", self.args.launch_timeout * 1000)
        # The process exists slightly before minui-list paints its first frame.
        time.sleep(0.75)


    # ---- screen verification -------------------------------------------
    #
    # VERIFY and VERIFY_WAIT compare the live screen against a reference image
    # and abort the run when it does not match. The point is to fail a macro at
    # the step that actually went wrong, rather than letting it press buttons
    # into the wrong menu for another minute and be diagnosed afterwards from
    # the wreckage.

    #: Mean absolute difference, per pixel, of a 64x48 greyscale reduction.
    #: Identical screens score 0; the same screen with different text scores a
    #: few units; a different screen scores tens.
    VERIFY_THRESHOLD = 10.0

    @staticmethod
    def _signature(path: Path, region=None):
        try:
            from PIL import Image
        except ImportError as error:
            raise ProtocolError(
                "VERIFY needs Pillow (pip install pillow)") from error
        with Image.open(path) as image:
            if region:
                x, y, width, height = region
                image = image.crop((x, y, x + width, y + height))
            return image.convert("L").resize((64, 48)).tobytes()

    @classmethod
    def _difference(cls, first: Path, second: Path, region=None) -> float:
        a = cls._signature(first, region)
        b = cls._signature(second, region)
        if len(a) != len(b):
            raise ProtocolError(
                f"cannot compare {first.name} with {second.name}: different sizes")
        return sum(abs(x - y) for x, y in zip(a, b)) / len(a)

    def _capture_frame(self, device) -> Path:
        """Grab the current screen to a PNG for comparison, without recording it
        as one of the run's deliberate screenshots."""
        info = key_values(self.protocol(device, "screen").stdout)
        remote_path = info.get("path", "")
        if not remote_path.startswith("/tmp/DevBridge-screen-"):
            raise ProtocolError(f"{device.alias}: invalid screenshot path")
        self.command_number += 1
        raw = self.session_dir / f"verify-{self.command_number:04d}-{device.alias}.bgra"
        self.transport.download(device, remote_path, raw)
        self.transport.run(device, f"rm -f -- {shlex.quote(remote_path)}")
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise ProtocolError("VERIFY needs ffmpeg to decode the screen")
        png = raw.with_suffix(".png")
        subprocess.run([
            ffmpeg, "-loglevel", "error", "-y", "-f", "rawvideo",
            "-pixel_format", info.get("format", "bgra"), "-video_size",
            f"{info['width']}x{info['height']}", "-i", str(raw),
            "-frames:v", "1", "-update", "1", str(png),
        ], check=True)
        raw.unlink()
        return png

    def verify(self, devices, reference: str, timeout_ms: int | None = None,
               threshold: float | None = None, region=None):
        self.ensure_session()
        expected = Path(reference).expanduser()
        if not expected.is_file():
            raise ProtocolError(f"VERIFY reference not found: {expected}")
        limit = self.VERIFY_THRESHOLD if threshold is None else threshold

        # VERIFY checks once. VERIFY_WAIT checks at the start, halfway through
        # the timeout and at the end, and only fails if all three miss.
        if timeout_ms is None:
            delays = [0.0]
        else:
            half = timeout_ms / 2000.0
            delays = [0.0, half, half]

        pending = {device.alias: device for device in devices}
        best: dict[str, float] = {}
        best_shot: dict[str, Path] = {}
        for index, delay in enumerate(delays):
            if delay:
                time.sleep(delay)
            for alias in list(pending):
                device = pending[alias]
                shot = self._capture_frame(device)
                score = self._difference(shot, expected, region)
                if alias not in best or score < best[alias]:
                    best[alias] = score
                    # Keep the closest miss. A failed VERIFY that leaves nothing
                    # behind forces the whole run to be repeated just to see
                    # what was on screen.
                    kept = self.session_dir / f"verify-miss-{expected.stem}-{alias}.png"
                    shot.replace(kept)
                    best_shot[alias] = kept
                else:
                    shot.unlink(missing_ok=True)
                if score <= limit:
                    kept = best_shot.pop(alias, None)
                    if kept:
                        kept.unlink(missing_ok=True)
                    print(f"{alias}: verified {expected.name} "
                          f"(difference {score:.2f}, attempt {index + 1})")
                    self.record("verify", device=alias, reference=str(expected),
                                difference=round(score, 3), attempt=index + 1,
                                matched=True)
                    del pending[alias]
            if not pending:
                return

        for alias, score in sorted(best.items()):
            if alias in pending:
                self.record("verify", device=alias, reference=str(expected),
                            difference=round(score, 3), matched=False)
        for alias, kept in best_shot.items():
            if alias in pending:
                print(f"{alias}: closest miss kept at {kept}", file=sys.stderr)
        failed = ", ".join(f"{alias} off by {best[alias]:.2f}"
                           for alias in sorted(pending))
        raise ProtocolError(
            f"screen does not match {expected.name}: {failed} "
            f"(threshold {limit:.2f})")

    def screenshot(self, devices, label=None):
        self.ensure_session()
        ffmpeg = shutil.which("ffmpeg")
        for device in devices:
            info = key_values(self.protocol(device, "screen").stdout)
            remote_path = info.get("path", "")
            if not remote_path.startswith("/tmp/DevBridge-screen-"):
                raise ProtocolError(f"{device.alias}: invalid screenshot path")
            self.command_number += 1
            stem = label or f"screen-{self.command_number:04d}"
            raw = self.session_dir / f"{stem}-{device.alias}.bgra"
            self.transport.download(device, remote_path, raw)
            self.transport.run(device, f"rm -f -- {shlex.quote(remote_path)}")
            output = raw
            if ffmpeg:
                png = raw.with_suffix(".png")
                command = [
                    ffmpeg, "-loglevel", "error", "-y", "-f", "rawvideo",
                    "-pixel_format", info.get("format", "bgra"), "-video_size",
                    f"{info['width']}x{info['height']}", "-i", str(raw),
                    "-frames:v", "1", "-update", "1", str(png),
                ]
                subprocess.run(command, check=True)
                raw.unlink()
                output = png
            print(f"{device.alias}: screenshot {output}")
            self.record("screenshot", device=device.alias, path=str(output), metadata=info)

    def performance(self, devices, duration_ms: int, interval_ms: int):
        self.ensure_session()
        for device in devices:
            path = self.session_dir / f"performance-{device.alias}-{int(time.time())}.csv"
            deadline = time.monotonic() + duration_ms / 1000
            rows = []
            while time.monotonic() < deadline:
                rows.append(key_values(self.protocol(device, "perf").stdout))
                time.sleep(interval_ms / 1000)
            keys = sorted({key for row in rows for key in row})
            with path.open("w", newline="", encoding="utf-8") as output:
                writer = csv.DictWriter(output, fieldnames=keys)
                writer.writeheader(); writer.writerows(rows)
            print(f"{device.alias}: {len(rows)} samples -> {path}")

    def inventory(self, devices, command: str, *arguments):
        for device in devices:
            result = self.protocol(device, command, *arguments)
            print(f"[{device.alias}]\n{result.stdout.rstrip()}")

    def execute(self, line: str):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            return True
        tokens = shlex.split(stripped, comments=True)
        if not tokens:
            return True
        prefix = None
        if ":" in tokens[0]:
            possible, command = tokens[0].split(":", 1)
            if possible == "all" or possible in self.devices:
                prefix, tokens[0] = possible, command
        command = tokens[0].upper()
        arguments = tokens[1:]
        if command in {"EXIT", "QUIT"}:
            return False
        if command == "SESSION":
            if not arguments:
                raise ProtocolError("SESSION requires a name")
            self.start_session(arguments[0], arguments[1] if len(arguments) > 1 else self.args.session_root)
            self.record("command", line=stripped)
            return True
        self.record("command", line=stripped)
        if command == "DISCOVER":
            self.discover(float(arguments[0]) if arguments else 5.0)
        elif command == "CONNECT":
            if not arguments:
                raise ProtocolError("CONNECT requires an address")
            self.connect(arguments[0], arguments[1] if len(arguments) > 1 else None)
        elif command == "CONNECT_ADB":
            if not arguments:
                raise ProtocolError("CONNECT_ADB requires a serial")
            self.connect_adb(arguments[0], arguments[1] if len(arguments) > 1 else None)
        elif command == "ALIAS":
            if len(arguments) != 2 or arguments[0] not in self.devices:
                raise ProtocolError("usage: ALIAS OLD NEW")
            device = self.devices.pop(arguments[0]); device.alias = arguments[1]
            self.devices[device.alias] = device
        elif command == "ALIAS_CMD":
            if len(arguments) < 2:
                raise ProtocolError("usage: ALIAS_CMD NAME COMMAND...")
            self.named_commands[arguments[0].upper()] = shlex.join(arguments[1:])
        elif command in self.named_commands:
            return self.execute(self.named_commands[command])
        elif command == "STATUS":
            self.status(self.selected(prefix or "all"))
        elif command == "TRM":
            devices = self.selected(prefix)
            shell_command = shlex.join(arguments)
            for device in devices:
                result = self.protocol(device, "terminal", shell_command, check=False)
                sys.stdout.write(result.stdout); sys.stderr.write(result.stderr)
                if result.returncode:
                    raise ProtocolError(f"{device.alias}: terminal returned {result.returncode}")
        elif command == "KEEP_SCREEN_ON":
            self.inventory(self.selected(prefix), "keep-screen-on")
        elif command == "GET_BTNS":
            self.inventory(self.selected(prefix), "buttons")
        elif command == "GET_PAKS":
            self.inventory(self.selected(prefix), "paks")
        elif command == "GET_EMUS":
            self.inventory(self.selected(prefix), "emus")
        elif command == "GET_ROM_LIST":
            self.inventory(self.selected(prefix), "roms")
        elif command == "GET_ROM_BY_SYSTEM":
            self.inventory(self.selected(prefix), "roms", arguments[0])
        elif command == "GET_ROM_SAVESTATES":
            if len(arguments) != 2:
                raise ProtocolError("usage: GET_ROM_SAVESTATES TAG ROM")
            self.inventory(self.selected(prefix), "savestates", arguments[0], arguments[1])
        elif command == "BTN":
            self.input_action(self.selected(prefix), arguments)
        elif command == "OPEN_GAME":
            if len(arguments) != 2:
                raise ProtocolError("usage: OPEN_GAME TAG ROM")
            self.open_game(self.selected(prefix), arguments[0], arguments[1])
        elif command == "OPEN_GAME_STATE":
            if len(arguments) != 3:
                raise ProtocolError("usage: OPEN_GAME_STATE TAG ROM SLOT")
            self.open_game(self.selected(prefix), arguments[0], arguments[1], arguments[2])
        elif command == "OPEN_RANDOM_GAME":
            if len(arguments) != 1:
                raise ProtocolError("usage: OPEN_RANDOM_GAME TAG")
            for device in self.selected(prefix):
                listing = self.protocol(device, "roms", arguments[0]).stdout.splitlines()
                if not listing:
                    raise ProtocolError(f"{device.alias}: no ROMs for {arguments[0]}")
                _tag, rom = random.choice(listing).split("\t", 1)
                self.open_game([device], arguments[0], rom)
        elif command == "OPEN_PAK":
            for device in self.selected(prefix):
                self.protocol(device, "open-pak", " ".join(arguments))
        elif command == "QUIT_GAME":
            self.quit_game(self.selected(prefix))
        elif command in ("VERIFY", "VERIFY_WAIT"):
            waiting = command == "VERIFY_WAIT"
            need = 2 if waiting else 1
            if len(arguments) < need:
                raise ProtocolError(
                    "usage: VERIFY REFERENCE [threshold=N] [region=X,Y,W,H]"
                    if not waiting else
                    "usage: VERIFY_WAIT REFERENCE TIMEOUT_MS"
                    " [threshold=N] [region=X,Y,W,H]")
            options = verify_options(arguments[need:])
            self.verify(self.selected(prefix), arguments[0],
                        int(arguments[1]) if waiting else None,
                        options.get("threshold"), options.get("region"))
        elif command == "SCREEN":
            self.screenshot(self.selected(prefix), arguments[0] if arguments else None)
        elif command == "WAIT":
            time.sleep(int(arguments[0]) / 1000)
        elif command == "WAIT_FOR":
            timeout = int(arguments[1]) if len(arguments) > 1 else self.args.launch_timeout * 1000
            self.wait_for(self.selected(prefix), arguments[0].lower(), timeout)
        elif command == "PERF":
            duration = int(arguments[0]) if arguments else 10000
            interval = int(arguments[1]) if len(arguments) > 1 else 1000
            self.performance(self.selected(prefix), duration, interval)
        else:
            raise ProtocolError(f"unknown command: {command}")
        return True

    def run_file(self, path: Path):
        failed = False
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            try:
                if not self.execute(line):
                    break
            except Exception as error:
                failed = True
                self.record("error", file=str(path), line=number, message=str(error))
                print(f"{path}:{number}: {error}", file=sys.stderr)
                if not self.args.keep_going:
                    break
        return 1 if failed else 0

    def repl(self):
        print("DevBridge protocol shell. Type HELP for commands, EXIT to leave.")
        while True:
            try:
                line = input("devbridge> ")
                if line.strip().upper() == "HELP":
                    print("CONNECT CONNECT_ADB STATUS GET_* BTN OPEN_GAME OPEN_RANDOM_GAME OPEN_PAK "
                          "QUIT_GAME SCREEN VERIFY VERIFY_WAIT PERF WAIT WAIT_FOR TRM SESSION EXIT")
                    continue
                if not self.execute(line):
                    return 0
            except EOFError:
                return 0
            except KeyboardInterrupt:
                print()
            except Exception as error:
                print(f"error: {error}", file=sys.stderr)


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--platform", default="tg5040")
    result.add_argument("--user", default="root")
    result.add_argument("--port", type=int, default=22)
    result.add_argument("--identity", type=Path)
    result.add_argument("--password-env", default="DEVBRIDGE_PASSWORD")
    result.add_argument("--connect-timeout", type=int, default=8)
    result.add_argument("--operation-timeout", type=int, default=120)
    result.add_argument("--launch-timeout", type=int, default=30)
    result.add_argument(
        "--command-delay-ms", type=int, default=100,
        help="minimum interval between protocol command starts per device (default: 100)",
    )
    result.add_argument("--session-root", default="devbridge-sessions")
    result.add_argument("--connect", action="append", default=[], metavar="ADDRESS[:ALIAS]")
    result.add_argument("--adb", action="append", default=[], metavar="SERIAL[=ALIAS]",
                        help="connect through a USB or network ADB device serial")
    result.add_argument("--interface", default="0.0.0.0")
    result.add_argument("--unicast", action="store_true")
    result.add_argument("--keep-going", action="store_true")
    sub = result.add_subparsers(dest="mode")
    sub.add_parser("shell")
    run = sub.add_parser("run")
    run.add_argument("file", type=Path)
    execute = sub.add_parser("exec")
    execute.add_argument("command", nargs=argparse.REMAINDER)
    return result


def main():
    args = parser().parse_args()
    runner = Runner(args)
    try:
        for item in args.connect:
            address, separator, alias = item.partition(":")
            runner.connect(address, alias if separator else None)
        for item in args.adb:
            serial, separator, alias = item.partition("=")
            runner.connect_adb(serial, alias if separator else None)
        if args.mode == "run":
            return runner.run_file(args.file)
        if args.mode == "exec":
            return 0 if runner.execute(shlex.join(args.command)) else 0
        return runner.repl()
    except ProtocolError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    finally:
        runner.close()


if __name__ == "__main__":
    sys.exit(main())
