#!/usr/bin/env python3
"""Listen for DevBridge discovery beacons on the local network.

The device multicasts a status line every few seconds while the beacon is
enabled. Multicast is used rather than broadcast because sending to a subnet
broadcast address needs SO_BROADCAST, which BusyBox nc does not set.

  devbridge-listen.py            # print beacons as they arrive
  devbridge-listen.py --first    # print the first device's IP and exit
  devbridge-listen.py --ssh      # print a ready-to-run ssh command and exit
"""

import argparse
import json
import socket
import sys
import time

GROUP = "239.255.42.99"
PORT = 42099


def parse(line):
    """Turn 'devbridge host=X ip=Y ...' into a dict."""
    fields = {}
    for token in line.split():
        if "=" in token:
            key, _, value = token.partition("=")
            fields[key] = value
    return fields


def ssh_command(address, fields):
    port = fields.get("port", "22")
    option = "" if port == "22" else f" -p {port}"
    return f"ssh{option} root@{address}"


def listen(timeout=None, interface="0.0.0.0", multicast=True):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("", PORT))
    if multicast:
        membership = socket.inet_aton(GROUP) + socket.inet_aton(interface)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
    if timeout:
        sock.settimeout(timeout)
    return sock


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--first", action="store_true",
                        help="print the first IP seen and exit")
    parser.add_argument("--ssh", action="store_true",
                        help="print an ssh command for the first device and exit")
    parser.add_argument("--timeout", type=float, default=None,
                        help="give up after this many seconds")
    parser.add_argument("--interface", default="0.0.0.0", metavar="ADDRESS",
                        help="IPv4 address of the LAN interface to join")
    parser.add_argument("--unicast", action="store_true",
                        help="receive a Windows relay without joining multicast")
    parser.add_argument("--json", action="store_true",
                        help="emit machine-readable beacon objects")
    args = parser.parse_args()

    try:
        sock = listen(args.timeout, args.interface, not args.unicast)
    except OSError as error:
        print(f"could not join {GROUP}:{PORT}: {error}", file=sys.stderr)
        return 1

    deadline = time.time() + args.timeout if args.timeout else None
    seen = set()
    while True:
        if deadline and time.time() > deadline:
            print("no beacon received", file=sys.stderr)
            return 1
        try:
            data, sender = sock.recvfrom(2048)
        except socket.timeout:
            print("no beacon received", file=sys.stderr)
            return 1

        line = data.decode("utf-8", "replace").strip()
        if not line.startswith("devbridge"):
            continue
        fields = parse(line)
        address = fields.get("ip") or sender[0]
        if address in ("unknown", ""):
            address = sender[0]

        if args.ssh:
            print(ssh_command(address, fields))
            return 0
        if args.first:
            if args.json:
                print(json.dumps({**fields, "ip": address}, sort_keys=True))
            else:
                print(address)
            return 0

        key = (address, fields.get("port"), fields.get("battery"),
               fields.get("charge"))
        if key in seen:
            continue
        seen.add(key)
        if args.json:
            print(json.dumps({**fields, "ip": address}, sort_keys=True),
                  flush=True)
        else:
            print(f"{fields.get('host', '?'):<12} {address:<15} "
                  f"port={fields.get('port', '?'):<5} "
                  f"battery={fields.get('battery', '?'):<5} "
                  f"{fields.get('charge', '?')}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
