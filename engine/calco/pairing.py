"""Pairing a phone: a network code (this computer's address) and a 6-digit password.

The code is the computer's IPv4 address in Crockford base 32 (7 characters, shown as XXXX-XXX):
short to type, no ambiguous letters, decoded by the phone back into the address. The password is
random, stored in the library folder, and required on every request that does not come from this
computer itself: uploads over Wi-Fi, and the cable link (the phone checks it too).
"""

from __future__ import annotations

import json
import secrets
import socket
from pathlib import Path

ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford: no I, L, O, U


def encode_ip(ip: str) -> str:
    n = int.from_bytes(socket.inet_aton(ip), "big")
    chars = "".join(ALPHABET[(n >> (5 * i)) & 31] for i in reversed(range(7)))
    return f"{chars[:4]}-{chars[4:]}"


def decode_code(code: str) -> str:
    s = code.upper().replace("-", "").replace(" ", "").replace("I", "1").replace("L", "1").replace("O", "0")
    if len(s) != 7 or any(c not in ALPHABET for c in s):
        raise ValueError("a network code has 7 characters, like K3F7-R2Q")
    n = 0
    for c in s:
        n = n * 32 + ALPHABET.index(c)
    if n >= 1 << 32:
        raise ValueError("not a valid network code")
    return socket.inet_ntoa(n.to_bytes(4, "big"))


class Pairing:
    def __init__(self, root: Path):
        self.path = Path(root) / "pairing.json"
        data = {}
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
        self.pin = str(data.get("pin") or "")
        if len(self.pin) != 6 or not self.pin.isdigit():
            self.new_pin()

    def new_pin(self) -> str:
        self.pin = f"{secrets.randbelow(1_000_000):06d}"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"pin": self.pin}), encoding="utf-8")
        return self.pin

    def check(self, pin: str | None) -> bool:
        return bool(pin) and secrets.compare_digest(str(pin), self.pin)
