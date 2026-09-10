#!/usr/bin/env python3
"""Relay DevBridge multicast discovery from Windows into a WSL2 NAT guest.

Run this script with Windows Python. It joins the device multicast group on
Windows, resolves the current WSL address with ``wsl.exe hostname -I``, and
forwards validated DevBridge datagrams to the WSL listener as UDP unicast.

  py devbridge-relay-wsl.py
  py devbridge-relay-wsl.py --distribution Ubuntu

Inside WSL, receive the relayed datagrams with:

  python3 devbridge-listen.py --unicast
"""

import argparse
import ipaddress
import socket
import subprocess
import sys
import time

GROUP = "239.255.42.99"
PORT = 42099


def first_ipv4(text):
    """Return the first non-loopback IPv4 address in wsl.exe output."""
    for token in text.replace("\x00", "").split():
        try:
            address = ipaddress.ip_address(token)
        except ValueError:
            continue
        if address.version == 4 and not address.is_loopback:
            return str(address)
    raise ValueError("wsl.exe returned no usable IPv4 address")


def resolve_wsl(distribution=None, executable="wsl.exe"):
    command = [executable]
    if distribution:
        command.extend(["--distribution", distribution])
    command.extend(["--exec", "hostname", "-I"])
    result = subprocess.run(command, check=True, capture_output=True, text=True,
                            timeout=10)
    return first_ipv4(result.stdout)


def multicast_socket(interface):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("", PORT))
    membership = socket.inet_aton(GROUP) + socket.inet_aton(interface)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
    return sock


def close_socket(sock):
    if sock is not None:
        try:
            sock.close()
        except OSError:
            pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--distribution", help="target WSL distribution name")
    parser.add_argument("--interface", default="0.0.0.0", metavar="ADDRESS",
                        help="Windows LAN IPv4 address used to join multicast")
    parser.add_argument("--target", metavar="ADDRESS",
                        help="fixed relay target instead of querying wsl.exe")
    parser.add_argument("--refresh", type=float, default=15,
                        help="seconds between WSL address refreshes")
    parser.add_argument("--rejoin-after", type=float, default=15, metavar="SECONDS",
                        help="rejoin multicast after this long without a beacon")
    parser.add_argument("--retry", type=float, default=2, metavar="SECONDS",
                        help="delay before retrying a failed multicast join")
    parser.add_argument("--quiet", action="store_true",
                        help="suppress per-beacon relay messages")
    args = parser.parse_args()

    outgoing = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    target = args.target
    resolved_at = 0.0
    incoming = None

    while True:
        if incoming is None:
            try:
                incoming = multicast_socket(args.interface)
                incoming.settimeout(args.rejoin_after)
                if not args.quiet:
                    print(f"joined {GROUP}:{PORT} on {args.interface}", flush=True)
            except OSError as error:
                print(f"could not join {GROUP}:{PORT}: {error}; retrying",
                      file=sys.stderr, flush=True)
                time.sleep(args.retry)
                continue

        try:
            data, sender = incoming.recvfrom(2048)
        except socket.timeout:
            if not args.quiet:
                print(f"no beacon for {args.rejoin_after:g}s; rejoining multicast",
                      file=sys.stderr, flush=True)
            close_socket(incoming)
            incoming = None
            continue
        except OSError as error:
            print(f"multicast receive failed: {error}; rejoining",
                  file=sys.stderr, flush=True)
            close_socket(incoming)
            incoming = None
            time.sleep(args.retry)
            continue

        if not data.startswith(b"devbridge "):
            continue

        now = time.monotonic()
        if not target or (not args.target and now - resolved_at >= args.refresh):
            try:
                target = resolve_wsl(args.distribution)
                resolved_at = now
            except (OSError, ValueError, subprocess.SubprocessError) as error:
                print(f"could not resolve WSL address: {error}", file=sys.stderr)
                target = None
                continue

        try:
            outgoing.sendto(data, (target, PORT))
        except OSError as error:
            print(f"could not relay to {target}:{PORT}: {error}",
                  file=sys.stderr, flush=True)
            if not args.target:
                target = None
            continue
        if not args.quiet:
            line = data.decode("utf-8", "replace").strip()
            print(f"{sender[0]} -> {target}:{PORT}  {line}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
