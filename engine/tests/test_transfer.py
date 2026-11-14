"""Cable transfer: the engine pulls a capture from a (fake) phone, as it does through usbmuxd."""

import json
import socket
import struct
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from calco import usb
from calco.library import Library
from calco.synth import make_synthetic_bundle


class FakePhone(BaseHTTPRequestHandler):
    """Speaks the same protocol as PhoneServer.swift."""

    root: Path = Path()
    sent: list = []

    def log_message(self, *args):
        pass

    def _send(self, body: bytes, status: int = 200):
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/calco/hello":
            return self._send(json.dumps({"name": "Test iPhone", "app": "0.2.0"}).encode())
        if self.path == "/calco/captures":
            caps = []
            for d in self.root.glob("*.calco"):
                m = json.loads((d / "manifest.json").read_text())
                caps.append({"id": m["id"], "name": m["name"], "sent": m["id"] in FakePhone.sent,
                             "files": {p.relative_to(d).as_posix(): p.stat().st_size for p in d.rglob("*") if p.is_file()}})
            return self._send(json.dumps(caps).encode())
        if self.path.startswith("/calco/file/"):
            cid, rel = self.path[len("/calco/file/"):].split("/", 1)
            p = self.root / f"{cid}.calco" / rel
            return self._send(p.read_bytes()) if p.exists() else self._send(b"", 404)
        self._send(b"", 404)

    def do_POST(self):
        if self.path.startswith("/calco/sent/"):
            FakePhone.sent.append(self.path.rsplit("/", 1)[1])
            return self._send(b"{}")
        self._send(b"", 404)


@pytest.fixture()
def phone(tmp_path):
    root = tmp_path / "phone"
    make_synthetic_bundle(root / "00000000-0000-0000-0000-000000000007.calco", views_per_ring=3, image_size=(320, 240), seed=7)
    FakePhone.root, FakePhone.sent = root, []
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakePhone)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_address[1]
    server.shutdown()


def test_usbmux_packet_layout():
    pkt = usb._packet({"MessageType": "ListDevices"}, tag=5)
    length, version, kind, tag = struct.unpack("<IIII", pkt[:16])
    assert (length, version, kind, tag) == (len(pkt), 1, 8, 5)
    assert b"ListDevices" in pkt


def test_cable_pull(tmp_path, phone):
    lib = Library(tmp_path / "lib")
    bridge = usb.UsbBridge(lib, devices=lambda: [{"id": 1, "link": "usb", "serial": "x"}],
                           connector=lambda dev, port: socket.create_connection(("127.0.0.1", phone)))
    bridge.poll_once()
    caps = lib.list_captures()
    assert len(caps) == 1 and caps[0]["frames"] == 6
    assert FakePhone.sent == ["00000000-0000-0000-0000-000000000007"]
    status = bridge.status()["phone"]
    assert status["name"] == "Test iPhone" and status["link"] == "usb" and status["transfer"] is None
    # nothing new: a second sync downloads nothing and leaves the library as it is
    bridge.poll_once()
    assert len(lib.list_captures()) == 1
    # deleted on the computer: must not come back from the phone
    lib.delete_capture(caps[0]["id"])
    bridge.poll_once()
    assert lib.list_captures() == []


def test_resume_after_interrupted_pull(tmp_path, phone):
    lib = Library(tmp_path / "lib")
    cid = "00000000-0000-0000-0000-000000000007"
    src = FakePhone.root / f"{cid}.calco"
    # pretend half the frames arrived in an earlier, interrupted sync
    for p in sorted((src / "frames").glob("*.jpg"))[:3]:
        dst = lib.incoming / f"{cid}.calco" / "frames" / p.name
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(p.read_bytes())
    requested = []
    original = usb.http

    def spy(connect, method, path, **kw):
        requested.append(path)
        return original(connect, method, path, **kw)

    usb.http = spy
    try:
        usb.UsbBridge(lib, devices=lambda: [{"id": 1, "link": "usb", "serial": "x"}],
                      connector=lambda dev, port: socket.create_connection(("127.0.0.1", phone))).poll_once()
    finally:
        usb.http = original
    frames = [r for r in requested if "/frames/" in r]
    assert len(frames) == 3  # only the missing half
    assert lib.list_captures()[0]["frames"] == 6


def test_no_phone_app_means_not_connected(tmp_path):
    lib = Library(tmp_path / "lib")

    def refuse(dev, port):
        raise usb.UsbError("phone refused the connection")

    bridge = usb.UsbBridge(lib, devices=lambda: [{"id": 1, "link": "usb", "serial": "x"}], connector=refuse)
    bridge.poll_once()
    assert bridge.status()["phone"] is None and bridge.available is True


def test_pairing_password_is_required_for_the_phone(tmp_path):
    from fastapi.testclient import TestClient

    from calco.pairing import decode_code, encode_ip
    from calco.server import create_app

    app = create_app(Library(tmp_path / "lib"))
    client = TestClient(app)
    info = client.get("/api/pairing").json()  # the computer itself sees the code and password
    pin = info["pin"]
    assert len(pin) == 6 and pin.isdigit()
    phone = {"X-Calco-Device": "Test iPhone"}
    assert client.get("/api/hello", headers=phone).status_code == 401
    assert client.get("/api/hello", headers={**phone, "X-Calco-Pin": "000000" if pin != "000000" else "111111"}).status_code == 401
    assert client.get("/api/hello", headers={**phone, "X-Calco-Pin": pin}).status_code == 200
    assert client.put("/api/captures/x/files/manifest.json", content=b"{}", headers=phone).status_code == 401
    new = client.post("/api/pairing/new").json()["pin"]
    assert client.get("/api/hello", headers={**phone, "X-Calco-Pin": pin}).status_code in (200, 401)
    assert client.get("/api/hello", headers={**phone, "X-Calco-Pin": new}).status_code == 200
    assert decode_code(encode_ip("192.168.1.211")) == "192.168.1.211"
