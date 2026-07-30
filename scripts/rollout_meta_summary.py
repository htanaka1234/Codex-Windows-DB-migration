import json
import os
import sqlite3
from pathlib import Path


DB = Path(os.environ.get("CODEX_STATE_DB", Path.home() / ".codex" / "state_5.sqlite"))


def first_payload(path: str):
    with Path(path).open("r", encoding="utf-8", errors="replace") as fh:
        obj = json.loads(fh.readline())
    payload = obj.get("payload", obj)
    return {
        "id": payload.get("id"),
        "cwd": payload.get("cwd"),
        "source": payload.get("source"),
        "thread_source": payload.get("thread_source"),
        "originator": payload.get("originator"),
        "cli_version": payload.get("cli_version"),
        "keys": sorted(payload.keys()),
    }


def main():
    con = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "select id,cwd,source,thread_source,rollout_path,updated_at_ms "
            "from threads where source='vscode' "
            "order by updated_at_ms desc, id desc limit 12"
        ).fetchall()
        for row in rows:
            print(json.dumps({
                "db": dict(row),
                "payload": first_payload(row["rollout_path"]),
            }, ensure_ascii=False, default=str))
    finally:
        con.close()


if __name__ == "__main__":
    main()
