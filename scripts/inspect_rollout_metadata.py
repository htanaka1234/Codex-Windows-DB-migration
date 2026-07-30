import json
import os
import sqlite3
from pathlib import Path


DB = Path(os.environ.get("CODEX_STATE_DB", Path.home() / ".codex" / "state_5.sqlite"))


def read_first_json(path: Path):
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        line = fh.readline()
    return json.loads(line)


def rows(con, sql, args=()):
    con.row_factory = sqlite3.Row
    return [dict(row) for row in con.execute(sql, args)]


def main():
    con = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        targets = rows(
            con,
            "select id,cwd,source,thread_source,rollout_path,substr(title,1,80) title "
            "from threads "
            "where cwd like '/mnt/%' or (source='vscode' and thread_source is null) "
            "order by updated_at_ms desc, id desc",
        )
    finally:
        con.close()
    for row in targets:
        path = Path(row["rollout_path"])
        meta = read_first_json(path)
        event = meta.get("event") if isinstance(meta, dict) else None
        payload = event if isinstance(event, dict) else meta
        print(json.dumps({
            "db": row,
            "meta_keys": sorted(meta.keys()) if isinstance(meta, dict) else None,
            "event_keys": sorted(event.keys()) if isinstance(event, dict) else None,
            "meta_id": meta.get("id") if isinstance(meta, dict) else None,
            "event_id": event.get("id") if isinstance(event, dict) else None,
            "payload_cwd": payload.get("cwd") if isinstance(payload, dict) else None,
            "payload_source": payload.get("source") if isinstance(payload, dict) else None,
            "payload_thread_source": payload.get("thread_source") if isinstance(payload, dict) else None,
            "payload_timestamp": payload.get("timestamp") if isinstance(payload, dict) else None,
            "first_line": meta,
        }, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
