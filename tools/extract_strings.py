"""List every translatable string, and check a catalogue against the source.

    python tools/extract_strings.py                 # report missing / unused for lang/zh-Hant.json
    python tools/extract_strings.py --write-missing # add missing keys with empty values

Strings are found by parsing the source with ``ast`` and collecting the first
argument of ``t()``, ``N()`` and ``plural()`` (both forms) wherever it is a
literal, plus the component labels and details in setup/catalog.json.
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ["Gokuk.py", "GokukSetup.py", "app", "setup"]


def literal(node) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def strings() -> set[str]:
    found: set[str] = set()
    files = []
    for entry in SOURCES:
        path = ROOT / entry
        files += [path] if path.is_file() else sorted(path.rglob("*.py"))
    for file in files:
        tree = ast.parse(file.read_text(encoding="utf-8"), filename=str(file))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.id if isinstance(node.func, ast.Name) else \
                node.func.attr if isinstance(node.func, ast.Attribute) else ""
            if name in ("t", "N") and node.args and (text := literal(node.args[0])):
                found.add(text)
            elif name == "plural" and len(node.args) >= 3:
                for arg in node.args[1:3]:
                    if text := literal(arg):
                        found.add(text)
    catalog = json.loads((ROOT / "setup" / "catalog.json").read_text(encoding="utf-8"))
    for component in catalog["components"]:
        found.add(component["label"])
        if component.get("detail"):
            found.add(component["detail"])
    return found


def main() -> None:
    lang = ROOT / "lang" / "zh-Hant.json"
    wanted = strings()
    existing = json.loads(lang.read_text(encoding="utf-8-sig")) if lang.exists() else {}
    missing = sorted(s for s in wanted if not existing.get(s))
    unused = sorted(k for k in existing if k not in wanted and not k.startswith("_"))
    print(f"{len(wanted)} strings, {len(missing)} missing, {len(unused)} unused")
    for s in missing:
        print("  MISSING:", json.dumps(s, ensure_ascii=False))
    for s in unused:
        print("  UNUSED: ", json.dumps(s, ensure_ascii=False))
    if "--write-missing" in sys.argv:
        for s in missing:
            existing.setdefault(s, "")
        lang.parent.mkdir(exist_ok=True)
        lang.write_text(json.dumps(dict(sorted(existing.items())), ensure_ascii=False, indent=1) + "\n",
                        encoding="utf-8")
    if "--json" in sys.argv:
        print(json.dumps(sorted(wanted), ensure_ascii=False, indent=0))


if __name__ == "__main__":
    main()
