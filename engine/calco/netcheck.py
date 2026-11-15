"""Read-only check of what stops the phone from reaching this computer over Wi-Fi (Windows).

Nothing here changes a setting: the UI shows the result and the steps the owner runs themselves
(tools/allow-wifi.ps1).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

_SCRIPT = r"""
$py = $env:CALCO_PY
$block = @(Get-NetFirewallApplicationFilter -ErrorAction SilentlyContinue | Where-Object { $_.Program -eq $py } |
  Get-NetFirewallRule -ErrorAction SilentlyContinue | Where-Object { $_.Enabled -eq 'True' -and $_.Direction -eq 'Inbound' -and $_.Action -eq 'Block' })
$allow = @(Get-NetFirewallApplicationFilter -ErrorAction SilentlyContinue | Where-Object { $_.Program -eq $py } |
  Get-NetFirewallRule -ErrorAction SilentlyContinue | Where-Object { $_.Enabled -eq 'True' -and $_.Direction -eq 'Inbound' -and $_.Action -eq 'Allow' })
$public = @(Get-NetConnectionProfile -ErrorAction SilentlyContinue | Where-Object { $_.NetworkCategory -eq 'Public' -and $_.IPv4Connectivity -ne 'NoTraffic' } | ForEach-Object { $_.InterfaceAlias })
@{ blocked = $block.Count; allowed = $allow.Count; public = $public } | ConvertTo-Json -Compress
"""


def python_exe() -> str:
    """The real interpreter Windows sees (a venv's python.exe only launches the base one)."""
    return getattr(sys, "_base_executable", None) or sys.executable


def check() -> dict:
    if os.name != "nt":
        return {"ok": True, "checked": False}
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", _SCRIPT],
                             capture_output=True, text=True, timeout=30,
                             env={**os.environ, "CALCO_PY": python_exe()},
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.strip()
        data = json.loads(out.splitlines()[-1])
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return {"ok": True, "checked": False}
    public = data.get("public") or []
    if isinstance(public, str):
        public = [public]
    blocked = int(data.get("blocked", 0)) > 0
    # Public networks drop incoming connections unless a rule explicitly allows them there
    problems = []
    if blocked:
        problems.append("Windows Firewall has a rule that blocks Calco (Python) from receiving connections.")
    if public:
        problems.append(f"The network '{', '.join(public)}' is set to Public, so Windows refuses connections from the phone.")
    return {"ok": not problems, "checked": True, "problems": problems, "python": python_exe(),
            "fix_script": str(Path(__file__).resolve().parents[2] / "tools" / "allow-wifi.ps1")}


class NetWatch:
    """Re-checks every few minutes in the background (the check takes a second or two)."""

    def __init__(self, every: float = 120.0):
        self.every = every
        self.result: dict = {"ok": True, "checked": False}
        self._wake = threading.Event()

    def start(self) -> "NetWatch":
        threading.Thread(target=self._loop, daemon=True, name="calco-netcheck").start()
        return self

    def recheck(self) -> None:
        self._wake.set()

    def _loop(self) -> None:
        while True:
            self.result = {**check(), "at": time.time()}
            self._wake.wait(self.every)
            self._wake.clear()
