"""Pull captures from the iPhone over the USB cable.

Apple's USB service (usbmuxd, "Apple Mobile Device Service" on Windows, installed with iTunes,
Apple Devices or Sideloadly) listens on 127.0.0.1:27015. It can open a TCP connection to a port
on a connected iPhone. The Calco app serves its captures on PHONE_PORT, so the computer pulls
whatever it does not have yet, as soon as the phone is plugged in and the app is open.

The same works without a cable when the phone is paired for Wi-Fi sync in Apple Devices/iTunes:
usbmuxd then lists it with ConnectionType "Network". Because the computer opens the connection,
the Windows firewall is not involved.
"""

from __future__ import annotations

import json
import plistlib
import socket
import struct
import threading
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

USBMUXD = ("127.0.0.1", 27015)
PHONE_PORT = 7879


class UsbError(OSError):
    pass


# ---------------------------------------------------------------- usbmux protocol

def _packet(message: dict, tag: int) -> bytes:
    body = plistlib.dumps(message)
    return struct.pack("<IIII", 16 + len(body), 1, 8, tag) + body  # length, version 1, plist, tag


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise UsbError("usbmuxd closed the connection")
        data += chunk
    return data


def _request(sock: socket.socket, message: dict, tag: int = 1) -> dict:
    message = {"ClientVersionString": "calco", "ProgName": "calco", **message}
    sock.sendall(_packet(message, tag))
    length = struct.unpack("<IIII", _recv_exact(sock, 16))[0]
    return plistlib.loads(_recv_exact(sock, length - 16))


def list_devices(timeout: float = 2.0) -> list[dict]:
    """[{"id": 3, "link": "usb" | "network", "serial": "..."}]. Raises UsbError if usbmuxd is missing."""
    try:
        with socket.create_connection(USBMUXD, timeout=timeout) as s:
            reply = _request(s, {"MessageType": "ListDevices"})
    except OSError as e:
        raise UsbError(f"Apple USB service not reachable: {e}") from e
    out = []
    for d in reply.get("DeviceList", []):
        props = d.get("Properties", {})
        out.append({"id": d.get("DeviceID", props.get("DeviceID")),
                    "link": "usb" if props.get("ConnectionType") == "USB" else "network",
                    "serial": props.get("SerialNumber", "")})
    return out


def usb_connect(device_id: int, port: int, timeout: float = 5.0) -> socket.socket:
    """A socket tunnelled to `port` on the device."""
    s = socket.create_connection(USBMUXD, timeout=timeout)
    try:
        swapped = ((port & 0xFF) << 8) | (port >> 8)  # usbmuxd wants the port in network byte order
        reply = _request(s, {"MessageType": "Connect", "DeviceID": device_id, "PortNumber": swapped})
    except OSError:
        s.close()
        raise
    if reply.get("Number", 1) != 0:
        s.close()
        raise UsbError(f"phone refused the connection (code {reply.get('Number')}): is the Calco app open?")
    return s


# ---------------------------------------------------------------- minimal HTTP over a socket

def http(connect: Callable[[], socket.socket], method: str, path: str, sink: Path | None = None,
         headers: dict | None = None, on_bytes: Callable[[int], None] | None = None) -> bytes:
    """One HTTP/1.1 request on a fresh connection. Streams the body to `sink` when given."""
    s = connect()
    try:
        s.settimeout(30)
        lines = [f"{method} {urllib.parse.quote(path)} HTTP/1.1", "Host: phone", "Connection: close", "Content-Length: 0"]
        lines += [f"{k}: {v}" for k, v in (headers or {}).items()]
        s.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = s.recv(65536)
            if not chunk:
                raise UsbError("phone closed the connection before answering")
            buf += chunk
        head, body = buf.split(b"\r\n\r\n", 1)
        status_line, *header_lines = head.decode("latin-1").split("\r\n")
        status = int(status_line.split()[1])
        hdrs = {k.strip().lower(): v.strip() for k, v in (line.split(":", 1) for line in header_lines if ":" in line)}
        length = int(hdrs.get("content-length", -1))
        if status != 200:
            raise UsbError(f"phone answered {status} for {path}")
        if sink is None:
            while length < 0 or len(body) < length:
                chunk = s.recv(65536)
                if not chunk:
                    break
                body += chunk
            return body
        sink.parent.mkdir(parents=True, exist_ok=True)
        tmp = sink.with_name(sink.name + ".part")
        got = len(body)
        with tmp.open("wb") as fh:
            fh.write(body)
            if on_bytes:
                on_bytes(len(body))
            while length < 0 or got < length:
                chunk = s.recv(1 << 20)
                if not chunk:
                    break
                fh.write(chunk)
                got += len(chunk)
                if on_bytes:
                    on_bytes(len(chunk))
        if length >= 0 and got != length:
            tmp.unlink(missing_ok=True)
            raise UsbError(f"transfer of {path} interrupted ({got} of {length} bytes)")
        tmp.replace(sink)
        return b""
    finally:
        s.close()


# ---------------------------------------------------------------- the bridge

@dataclass
class PhoneLink:
    name: str
    link: str  # "usb" | "network"
    seen: float
    transfer: dict | None = None  # {"capture": name, "done": bytes, "total": bytes, "index": i, "count": n}
    error: str | None = None


class UsbBridge:
    """Background thread: watch for phones on usbmuxd and pull new captures into the library."""

    def __init__(self, library, interval: float = 2.0,
                 devices: Callable[[], list[dict]] = list_devices,
                 connector: Callable[[int, int], socket.socket] = usb_connect):
        self.library = library
        self.interval = interval
        self._devices = devices
        self._connector = connector
        self.available: bool | None = None  # usbmuxd present
        self.pin: Callable[[], str] = lambda: ""  # pairing password, set by the server
        self.phone: PhoneLink | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()

    def start(self) -> "UsbBridge":
        threading.Thread(target=self._loop, daemon=True, name="calco-usb").start()
        return self

    def stop(self) -> None:
        self._stop.set()

    def status(self) -> dict:
        with self._lock:
            p = self.phone
            return {"usb_service": self.available,
                    "phone": None if p is None else {"name": p.name, "link": p.link, "seen": round(time.time() - p.seen, 1),
                                                     "transfer": p.transfer, "error": p.error}}

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception as e:  # never let the watcher die
                with self._lock:
                    if self.phone:
                        self.phone.error = str(e)
            self._stop.wait(self.interval)

    def poll_once(self) -> None:
        try:
            devices = self._devices()
            self.available = True
        except UsbError:
            self.available = False
            devices = []
        # prefer the cable when the same phone shows up twice
        devices.sort(key=lambda d: d["link"] != "usb")
        for dev in devices:
            connect = lambda d=dev: self._connector(d["id"], PHONE_PORT)  # noqa: E731
            link_header = {"X-Calco-Link": "cable" if dev["link"] == "usb" else "wifi-sync", "X-Calco-Pin": self.pin()}
            try:
                hello = json.loads(http(connect, "GET", "/calco/hello", headers=link_header))
            except UsbError as e:
                if "answered 401" in str(e):  # Calco is open but paired with another password
                    with self._lock:
                        self.phone = PhoneLink("iPhone", dev["link"], time.time(), error="not paired")
                    return
                continue  # app not open on this device
            except (OSError, ValueError):
                continue
            with self._lock:
                prev = self.phone
                self.phone = PhoneLink(hello.get("name", "iPhone"), dev["link"], time.time())
                if prev and prev.name == self.phone.name:
                    self.phone.transfer = prev.transfer
            self._sync(connect, link_header)
            return
        with self._lock:
            if self.phone and time.time() - self.phone.seen > 3 * self.interval + 2:
                self.phone = None

    def _sync(self, connect, link_header: dict) -> None:
        captures = json.loads(http(connect, "GET", "/calco/captures", headers=link_header))
        # "sent" captures were delivered before (by cable or Wi-Fi): if they are gone from the library,
        # it is because they were deleted here, so they are not fetched again
        todo = [c for c in captures if not c.get("sent") and not self.library.capture_path(c["id"]).exists()]
        for c in captures:  # already here but the phone does not know yet (e.g. sent over Wi-Fi meanwhile)
            if not c.get("sent") and self.library.capture_path(c["id"]).exists():
                try:
                    http(connect, "POST", f"/calco/sent/{c['id']}", headers=link_header)
                except OSError:
                    pass
        for i, cap in enumerate(todo):
            incoming = self.library.incoming / f"{cap['id']}.calco"
            files = cap["files"]  # {relative path: size}
            total = sum(files.values())
            done = [sum(size for rel, size in files.items()
                        if (incoming / rel).exists() and (incoming / rel).stat().st_size == size)]

            def tick(n: int) -> None:
                done[0] += n
                with self._lock:
                    if self.phone:
                        self.phone.seen = time.time()
                        self.phone.transfer = {"capture": cap.get("name", cap["id"]), "done": done[0], "total": total,
                                               "index": i + 1, "count": len(todo)}

            tick(0)
            # manifest last: a capture is only complete once its manifest has arrived
            for rel in sorted(files, key=lambda r: (r == "manifest.json", r)):
                target = incoming / rel
                if target.exists() and target.stat().st_size == files[rel] and rel != "manifest.json":
                    continue
                if ".." in Path(rel).parts or Path(rel).is_absolute():
                    raise UsbError(f"refusing unsafe path from phone: {rel}")
                http(connect, "GET", f"/calco/file/{cap['id']}/{rel}", sink=target, headers=link_header, on_bytes=tick)
            self.library.complete_incoming(cap["id"])
            try:
                http(connect, "POST", f"/calco/sent/{cap['id']}", headers=link_header)
            except OSError:
                pass  # the phone just will not show the tick until the next sync
        with self._lock:
            if self.phone:
                self.phone.transfer = None
                self.phone.error = None
