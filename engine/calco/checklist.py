"""The field test sheet (docs/collaudo.md) as a checklist the desktop app can tick off.

The markdown stays the single source: sections are "## " headings, items are "- ☐ **ID** text"
lines and "| ☐ Case | How | ... |" table rows. Ticks and notes live in the library folder.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .paths import field_test_sheet

SHEET = field_test_sheet()
_ITEM = re.compile(r"^- ☐ \*\*(?P<id>[A-Z]\d+[a-z]?)\*\* (?P<text>.+)$")
_ROW = re.compile(r"^\| ☐ (?P<case>[^|]+)\|(?P<how>[^|]*)\|")
_PLAIN = re.compile(r"^- ☐ (?P<text>.+)$")  # list items without an id (the on-site list)


def parse(path: Path = SHEET) -> list[dict]:
    sections: list[dict] = []
    current = {"title": "", "items": [], "notes": []}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            if current["items"] or current["notes"]:
                sections.append(current)
            current = {"title": line[3:].strip(), "items": [], "notes": []}
        elif m := _ITEM.match(line):
            current["items"].append({"id": m["id"], "text": m["text"].strip()})
        elif m := _PLAIN.match(line):
            current["items"].append({"id": f"{current['title'][:1]}-{len(current['items']) + 1}", "text": m["text"].strip()})
        elif m := _ROW.match(line):
            case = m["case"].strip()
            current["items"].append({"id": f"{current['title'][:1]}-{case}", "text": f"**{case}** — {m['how'].strip()}"})
        elif line.startswith("**") and line.endswith("**"):
            current["items"].append({"id": None, "text": line.strip("*"), "heading": True})
    if current["items"] or current["notes"]:
        sections.append(current)
    seen: set[str] = set()
    for section in sections:  # ids must be unique even if the sheet repeats one
        for item in section["items"]:
            if item.get("id"):
                base, k = item["id"], 2
                while item["id"] in seen:
                    item["id"], k = f"{base}-{k}", k + 1
                seen.add(item["id"])
    return [s for s in sections if any(i.get("id") for i in s["items"])]


class Checklist:
    def __init__(self, root: Path):
        self.path = Path(root) / "checklist.json"

    def state(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def set(self, item: str, done: bool | None = None, note: str | None = None) -> dict:
        data = self.state()
        entry = data.get(item, {"done": False, "note": ""})
        if done is not None:
            entry["done"] = bool(done)
        if note is not None:
            entry["note"] = note[:2000]
        data[item] = entry
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)
        return entry

    def reset(self) -> None:
        self.path.unlink(missing_ok=True)
