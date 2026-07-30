from __future__ import annotations

import json
import os
from pathlib import Path


CODEX_HOME = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))


def read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    bad = 0
    if not path.exists():
        print({"path": str(path), "exists": False})
        return rows
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except Exception:
            bad += 1
            continue
        if isinstance(value, dict):
            rows.append(value)
    print({"path": str(path), "rows": len(rows), "bad": bad})
    if rows:
        print({"first_keys": sorted(rows[0].keys())})
        print({"last_keys": sorted(rows[-1].keys())})
        for row in rows[-5:]:
            print({"tail_row": row})
    return rows


def inspect_global(path: Path) -> None:
    if not path.exists():
        print({"path": str(path), "exists": False})
        return
    data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    print({"path": str(path), "type": type(data).__name__, "keys": sorted(data.keys())})
    if not isinstance(data, dict):
        return
    for key, value in data.items():
        if isinstance(value, list):
            first = value[0] if value else None
            print(
                {
                    key: {
                        "type": "list",
                        "len": len(value),
                        "first_keys": sorted(first.keys())
                        if isinstance(first, dict)
                        else type(first).__name__
                        if first is not None
                        else None,
                        "values": value if key in {"active-workspace-roots", "electron-saved-workspace-roots", "project-order"} else None,
                    }
                }
            )
        elif isinstance(value, dict):
            print({key: {"type": "dict", "len": len(value), "keys": sorted(value.keys())[:40]}})
        else:
            print({key: {"type": type(value).__name__, "value": str(value)[:120]}})


def main() -> None:
    read_jsonl(CODEX_HOME / "session_index.jsonl")
    inspect_global(CODEX_HOME / ".codex-global-state.json")


if __name__ == "__main__":
    main()
