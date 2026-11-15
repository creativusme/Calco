"""Where Calco's own files are: the source checkout, or the folder the Windows installer put them in.

The installed runtime mirrors the repository layout under CALCO_HOME:
    desktop/dist/        the UI the engine serves
    docs/collaudo.md     the field test sheet
    tools/allow-wifi.ps1 the Wi-Fi fix
    tools/colmap, tools/openmvs   the reconstruction tools, already unpacked (read-only)
Everything that changes (library, downloaded tools, splat engine) lives in the user's folders instead.
"""

from __future__ import annotations

import os
from pathlib import Path


def home() -> Path:
    env = os.environ.get("CALCO_HOME")
    return Path(env) if env else Path(__file__).resolve().parents[2]


def installed() -> bool:
    return bool(os.environ.get("CALCO_HOME"))


def ui_dir() -> Path:
    return home() / "desktop" / "dist"


def field_test_sheet() -> Path:
    return home() / "docs" / "collaudo.md"


def wifi_fix_script() -> Path:
    return home() / "tools" / "allow-wifi.ps1"


def bundled_tools() -> Path:
    """Tools shipped with the installer (read-only); in a checkout this folder holds no tools."""
    return home() / "tools"
