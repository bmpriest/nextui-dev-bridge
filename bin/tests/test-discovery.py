#!/usr/bin/env python3

import importlib.util
import pathlib


ROOT = pathlib.Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


listener = load("devbridge_listener", ROOT / "tools/devbridge-listen.py")
relay = load("devbridge_relay", ROOT / "tools/devbridge-relay-wsl.py")

fields = listener.parse(
    "devbridge host=BRICK ip=192.168.0.144 port=22 battery=80% "
    "charge=charging sftp=yes"
)
assert fields["host"] == "BRICK"
assert fields["sftp"] == "yes"
assert listener.ssh_command(fields["ip"], fields) == "ssh root@192.168.0.144"

fields["port"] = "2222"
assert listener.ssh_command(fields["ip"], fields) == \
    "ssh -p 2222 root@192.168.0.144"

assert relay.first_ipv4("172.27.1.5  fe80::1\n") == "172.27.1.5"
assert relay.first_ipv4("\x00172.30.9.2\x00 \x00") == "172.30.9.2"
try:
    relay.first_ipv4("fe80::1")
except ValueError:
    pass
else:
    raise AssertionError("IPv6-only output should not select a relay target")


class ClosingSocket:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


closing = ClosingSocket()
relay.close_socket(closing)
assert closing.closed
relay.close_socket(None)

print("DevBridge discovery tests passed")
