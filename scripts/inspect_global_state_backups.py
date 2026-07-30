import json
import os
from pathlib import Path


HOME = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
paths = [HOME / ".codex-global-state.json", HOME / ".codex-global-state.json.bak"]
paths.extend(sorted(HOME.glob("..codex-global-state.json.tmp-*")))


def main():
    for path in paths:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            print({"path": str(path), "error": str(exc)})
            continue
        hints = data.get("thread-workspace-root-hints", {})
        saved = data.get("electron-saved-workspace-roots", [])
        order = data.get("project-order", [])
        active = data.get("active-workspace-roots", [])
        print(json.dumps({
            "path": str(path),
            "mtime": path.stat().st_mtime,
            "hints_count": len(hints),
            "hints_sample": dict(list(hints.items())[:5]),
            "saved": saved,
            "order": order,
            "active": active,
        }, ensure_ascii=False))


if __name__ == "__main__":
    main()
