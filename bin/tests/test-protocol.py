#!/usr/bin/env python3

import importlib.util
from pathlib import Path
import tempfile
import types
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("devbridge_protocol", ROOT / "tools/devbridge.py")
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class ProtocolTests(unittest.TestCase):
    def runner(self, root):
        runner = object.__new__(module.Runner)
        runner.args = types.SimpleNamespace(
            session_root=root, launch_timeout=1, port=22, platform="tg5040"
        )
        runner.transport = None
        runner.devices = {"brick": module.Device("192.0.2.1", "brick", 22, "tg5040")}
        runner.named_commands = {}
        runner.session_dir = None
        runner.events = None
        runner.command_number = 0
        runner.last_protocol_start = {}
        runner.calls = []

        def protocol(device, command, *arguments, **_kwargs):
            runner.calls.append((device.alias, command, *arguments))
            return types.SimpleNamespace(stdout="ok=ready\n", stderr="", returncode=0)

        runner.protocol = protocol
        return runner

    def test_key_values(self):
        self.assertEqual(module.key_values("a=1\nb=two=parts\nignored"),
                         {"a": "1", "b": "two=parts"})

    def test_handshake_marker_can_follow_login_banner(self):
        output = "Tina Linux (Neptune)\nDEVBRIDGE/1\nprotocol=1\n"
        self.assertTrue(module.has_protocol_marker(output))
        self.assertFalse(module.has_protocol_marker("DEVBRIDGE/10\n"))

    def test_default_command_pacing(self):
        self.assertEqual(module.parser().parse_args([]).command_delay_ms, 100)

    def test_marked_output_discards_login_banner(self):
        output = "Tina Linux\n" + module.OUTPUT_MARKER + "\nplatform=tg5040\n"
        self.assertEqual(module.marked_output(output), "platform=tg5040\n")

    def test_scp_uses_uppercase_port_option(self):
        transport = object.__new__(module.SSHTransport)
        transport.config = Path("/tmp/config")
        self.assertEqual(transport.options(2222, scp=True)[-2:], ["-P", "2222"])
        self.assertEqual(transport.options(2222)[-2:], ["-p", "2222"])

    def test_adb_transport_uses_serial_for_shell_and_pull(self):
        transport = object.__new__(module.SSHTransport)
        transport.args = types.SimpleNamespace(operation_timeout=5)
        device = module.Device("brick-serial", "brick", None, "tg5040", "adb")
        completed = types.SimpleNamespace(returncode=0, stdout="ok\n", stderr="")
        with mock.patch.object(module.subprocess, "run", return_value=completed) as run:
            transport.run(device, "printf hello")
            self.assertEqual(
                run.call_args.args[0],
                ["adb", "-s", "brick-serial", "shell", "sh -c 'printf hello'"],
            )
            transport.download(device, "/tmp/screen", Path("/tmp/local-screen"))
            self.assertEqual(
                run.call_args.args[0],
                ["adb", "-s", "brick-serial", "pull", "/tmp/screen", "/tmp/local-screen"],
            )

    def test_chord_order_and_release(self):
        with tempfile.TemporaryDirectory() as root:
            runner = self.runner(root)
            with mock.patch.object(module.time, "sleep"):
                runner.execute("brick:BTN HOLD L1,R1 5")
            self.assertEqual(
                runner.calls,
                [
                    ("brick", "input", "press", "l1"),
                    ("brick", "input", "press", "r1"),
                    ("brick", "input", "release", "r1"),
                    ("brick", "input", "release", "l1"),
                ],
            )
            runner.events.close()

    def test_named_command_and_session(self):
        with tempfile.TemporaryDirectory() as root:
            runner = self.runner(root)
            runner.execute(f"SESSION test-{{date}} {root}")
            runner.execute("ALIAS_CMD READY STATUS")
            runner.execute("READY")
            self.assertTrue(runner.session_dir.name.startswith("test-"))
            self.assertEqual(runner.calls[-1][1], "status")
            runner.events.close()

    def test_quit_game_selects_fifth_menu_row(self):
        with tempfile.TemporaryDirectory() as root:
            runner = self.runner(root)
            actions = []
            runner.input_action = lambda _devices, arguments: actions.append(arguments)
            runner.wait_for = lambda *_arguments: None
            with mock.patch.object(module.time, "sleep"):
                runner.quit_game(list(runner.devices.values()))
            self.assertEqual(actions[0], ["menu", "120"])
            self.assertEqual(actions[1:5], [["down", "80"]] * 4)
            self.assertEqual(actions[5], ["a", "120"])

    def test_savestate_inventory_and_launch(self):
        with tempfile.TemporaryDirectory() as root:
            runner = self.runner(root)
            runner.wait_for = lambda *_arguments: None
            rom = "Game Boy Advance (GBA)/Bob's Test.zip"
            runner.execute(f'GET_ROM_SAVESTATES GBA "{rom}"')
            runner.execute(f'OPEN_GAME_STATE GBA "{rom}" 3')
            self.assertEqual(runner.calls[0], ("brick", "savestates", "GBA", rom))
            self.assertEqual(
                runner.calls[1], ("brick", "open-game-state", "GBA", rom, "3")
            )
            runner.events.close()


if __name__ == "__main__":
    unittest.main()
