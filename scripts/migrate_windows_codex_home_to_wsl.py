import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from codex_path_styles import strip_long_windows_prefix, to_wsl_path


DEFAULT_SOURCE_HOME = Path(os.environ.get("CODEX_WINDOWS_HOME", Path.home() / ".codex"))
DEFAULT_DEST_HOME = Path(os.environ.get("CODEX_WSL_HOME", Path.home() / ".codex"))
DEFAULT_BACKUP_ROOT = Path("/mnt/c/src/.codex_bak")

TABLES = [
    "threads",
    "thread_dynamic_tools",
    "thread_goals",
    "thread_spawn_edges",
    "stage1_outputs",
    "agent_jobs",
    "agent_job_items",
    "remote_control_enrollments",
]

def quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def connect(path: Path, readonly: bool = False) -> sqlite3.Connection:
    if readonly:
        con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    else:
        con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    return con


def table_columns(con: sqlite3.Connection, db: str, table: str) -> list[str]:
    return [row[1] for row in con.execute(f"pragma {db}.table_info({quote(table)})")]


def table_count(con: sqlite3.Connection, db: str, table: str) -> int:
    return con.execute(f"select count(*) from {db}.{quote(table)}").fetchone()[0]


def norm_wsl(path: str | None) -> str:
    if not path:
        return ""
    return to_wsl_path(path)


def iso_from_ms(ms):
    if ms is None:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def load_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def load_index(path: Path) -> dict[str, dict]:
    rows = {}
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if isinstance(row, dict) and row.get("id"):
                rows[row["id"]] = row
    return rows


def write_index(path: Path, rows: list[dict]):
    tmp = path.with_suffix(".jsonl.tmp-migrate-wsl")
    with tmp.open("w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    tmp.replace(path)


def read_session_meta(path: Path):
    first = path.open("rb").readline()
    if not first:
        raise ValueError("empty file")
    obj = json.loads(first.decode("utf-8"))
    if obj.get("type") != "session_meta" or not isinstance(obj.get("payload"), dict):
        raise ValueError("first line is not session_meta")
    return obj["payload"]


def collect_source_sessions(source_home: Path, dest_home: Path):
    source_sessions = source_home / "sessions"
    dest_sessions = dest_home / "sessions"
    id_to_dest = {}
    copy_plan = []
    errors = []
    for src in sorted(source_sessions.glob("**/rollout-*.jsonl")):
        try:
            payload = read_session_meta(src)
        except Exception as exc:
            errors.append({"path": str(src), "error": str(exc)})
            continue
        thread_id = payload.get("id")
        if not thread_id:
            errors.append({"path": str(src), "error": "session_meta.payload.id is missing"})
            continue
        rel = src.relative_to(source_sessions)
        dst = dest_sessions / rel
        id_to_dest[thread_id] = dst
        if not dst.exists():
            copy_plan.append({"src": src, "dst": dst, "id": thread_id})
    return id_to_dest, copy_plan, errors


def visible_user_threads(con: sqlite3.Connection):
    return [
        dict(row)
        for row in con.execute(
            "select id,title,updated_at_ms,cwd from threads "
            "where source='vscode' and thread_source='user' and archived=0 and preview<>'' "
            "order by updated_at_ms asc, id asc"
        )
    ]


def summarize_home(home: Path):
    db = home / "state_5.sqlite"
    summary = {
        "home": str(home),
        "state_db_exists": db.exists(),
        "session_index_exists": (home / "session_index.jsonl").exists(),
        "global_state_exists": (home / ".codex-global-state.json").exists(),
        "session_files": len(list((home / "sessions").glob("**/rollout-*.jsonl"))) if (home / "sessions").exists() else 0,
    }
    if db.exists():
        with connect(db, readonly=True) as con:
            summary["threads"] = table_count(con, "main", "threads")
            summary["visible_user_threads"] = con.execute(
                "select count(*) from threads where source='vscode' and thread_source='user' and archived=0 and preview<>''"
            ).fetchone()[0]
    return summary


def backup_path(path: Path, backup_root: Path, stamp: str) -> Path:
    rel_name = path.name if path.name else "codex-home"
    return backup_root / f"{rel_name}.before-wsl-home-migrate-{stamp}"


def backup_file(path: Path, backup_root: Path, stamp: str) -> Path | None:
    if not path.exists():
        return None
    dst = backup_path(path, backup_root, stamp)
    if path.is_dir():
        shutil.copytree(path, dst)
    else:
        shutil.copy2(path, dst)
    return dst


def merge_db(source_home: Path, dest_home: Path, id_to_dest: dict[str, Path], backup_root: Path, stamp: str):
    source_db = source_home / "state_5.sqlite"
    dest_db = dest_home / "state_5.sqlite"
    backup = backup_file(dest_db, backup_root, stamp)
    for sidecar in [dest_db.with_name("state_5.sqlite-wal"), dest_db.with_name("state_5.sqlite-shm")]:
        backup_file(sidecar, backup_root, stamp)

    src = connect(source_db, readonly=True)
    con = connect(dest_db)
    try:
        con.execute("pragma foreign_keys=off")
        inserted = {}
        for table in TABLES:
            dst_cols = table_columns(con, "main", table)
            src_cols = [row[1] for row in src.execute(f"pragma table_info({quote(table)})")]
            if not dst_cols or not src_cols:
                continue
            common = [col for col in dst_cols if col in src_cols]
            before = table_count(con, "main", table)
            cols_sql = ", ".join(quote(col) for col in common)
            placeholders = ", ".join("?" for _ in common)
            select_sql = ", ".join(quote(col) for col in common)
            source_rows = src.execute(
                f"select {select_sql} from {quote(table)}"
            ).fetchall()
            con.executemany(
                f"insert or ignore into main.{quote(table)} ({cols_sql}) values ({placeholders})",
                [tuple(row[col] for col in common) for row in source_rows],
            )
            after = table_count(con, "main", table)
            inserted[table] = after - before

        for thread_id, path in id_to_dest.items():
            con.execute("update threads set rollout_path=? where id=?", (str(path), thread_id))

        con.commit()
        fk = con.execute("pragma foreign_key_check").fetchall()
        quick = [row[0] for row in con.execute("pragma quick_check")]
        integrity = [row[0] for row in con.execute("pragma integrity_check")]
        con.execute("pragma foreign_keys=on")
        con.commit()
        return {
            "backup": str(backup) if backup else None,
            "inserted": inserted,
            "foreign_key_error_count": len(fk),
            "foreign_key_check_sample": [tuple(row) for row in fk[:10]],
            "quick_check": quick,
            "integrity_check": integrity,
        }
    finally:
        con.close()
        src.close()


def rebuild_ui_state(source_home: Path, dest_home: Path, backup_root: Path, stamp: str):
    source_index = load_index(source_home / "session_index.jsonl")
    dest_index = load_index(dest_home / "session_index.jsonl")
    index_by_id = {**source_index, **dest_index}

    with connect(dest_home / "state_5.sqlite", readonly=True) as con:
        threads = visible_user_threads(con)

    rows = []
    hints = {}
    roots = []
    seen_roots = set()
    for thread in threads:
        existing = index_by_id.get(thread["id"], {})
        rows.append(
            {
                "id": thread["id"],
                "thread_name": existing.get("thread_name") or (thread["title"] or thread["id"])[:120],
                "updated_at": existing.get("updated_at") or iso_from_ms(thread["updated_at_ms"]),
            }
        )
        root = norm_wsl(thread["cwd"])
        hints[thread["id"]] = root
        key = root.lower()
        if key not in seen_roots:
            seen_roots.add(key)
            roots.append(root)

    index_backup = backup_file(dest_home / "session_index.jsonl", backup_root, stamp)
    write_index(dest_home / "session_index.jsonl", rows)

    source_state = load_json(source_home / ".codex-global-state.json", {})
    dest_state_path = dest_home / ".codex-global-state.json"
    dest_state = load_json(dest_state_path, {})
    state = {**source_state, **dest_state}

    saved = []
    for key in ("active-workspace-roots", "electron-saved-workspace-roots", "project-order"):
        for value in source_state.get(key, []) + dest_state.get(key, []) + roots:
            if isinstance(value, str):
                root = norm_wsl(value)
                if root.lower() not in {item.lower() for item in saved}:
                    saved.append(root)

    active = dest_state.get("active-workspace-roots") or source_state.get("active-workspace-roots") or ["/mnt/c/src/.codex_bak"]
    state["active-workspace-roots"] = [norm_wsl(item) for item in active if isinstance(item, str)]
    state["electron-saved-workspace-roots"] = saved
    state["project-order"] = saved
    state["thread-workspace-root-hints"] = hints

    global_backup = backup_file(dest_state_path, backup_root, stamp)
    tmp = dest_state_path.with_suffix(".json.tmp-migrate-wsl")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(dest_state_path)

    return {
        "session_index_backup": str(index_backup) if index_backup else None,
        "global_state_backup": str(global_backup) if global_backup else None,
        "session_index_rows": len(rows),
        "thread_workspace_root_hints": len(hints),
        "saved_workspace_roots": len(saved),
    }


def main() -> int:
    mode = "dry-run"
    source_home = DEFAULT_SOURCE_HOME
    dest_home = DEFAULT_DEST_HOME
    backup_root = DEFAULT_BACKUP_ROOT
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in {"dry-run", "apply"}:
            mode = arg
        elif arg == "--source-home":
            i += 1
            source_home = Path(args[i])
        elif arg == "--dest-home":
            i += 1
            dest_home = Path(args[i])
        elif arg == "--backup-root":
            i += 1
            backup_root = Path(args[i])
        else:
            raise SystemExit(
                "usage: migrate_windows_codex_home_to_wsl.py [dry-run|apply] "
                "[--source-home PATH] [--dest-home PATH] [--backup-root PATH]"
            )
        i += 1

    id_to_dest, copy_plan, errors = collect_source_sessions(source_home, dest_home)
    with connect(source_home / "state_5.sqlite", readonly=True) as src, connect(dest_home / "state_5.sqlite", readonly=True) as dst:
        src_ids = {row[0] for row in src.execute("select id from threads")}
        dst_ids = {row[0] for row in dst.execute("select id from threads")}
        src_visible_ids = {
            row[0]
            for row in src.execute(
                "select id from threads where source='vscode' and thread_source='user' and archived=0 and preview<>''"
            )
        }
        dst_visible_ids = {
            row[0]
            for row in dst.execute(
                "select id from threads where source='vscode' and thread_source='user' and archived=0 and preview<>''"
            )
        }

    plan = {
        "mode": mode,
        "source": summarize_home(source_home),
        "dest": summarize_home(dest_home),
        "source_threads_missing_from_dest": len(src_ids - dst_ids),
        "dest_threads_not_in_source": len(dst_ids - src_ids),
        "source_session_files_with_valid_meta": len(id_to_dest),
        "session_files_to_copy": len(copy_plan),
        "session_meta_errors": errors[:20],
        "expected_visible_user_threads_after_merge": len(src_visible_ids | dst_visible_ids),
    }
    print(json.dumps({"plan": plan}, ensure_ascii=False, indent=2))
    if mode == "dry-run":
        return 0

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_root.mkdir(parents=True, exist_ok=True)
    for item in copy_plan:
        item["dst"].parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item["src"], item["dst"])

    db_result = merge_db(source_home, dest_home, id_to_dest, backup_root, stamp)
    ui_result = rebuild_ui_state(source_home, dest_home, backup_root, stamp)

    print(
        json.dumps(
            {
                "applied": {
                    "session_files_copied": len(copy_plan),
                    "db": db_result,
                    "ui": ui_result,
                    "dest_after": summarize_home(dest_home),
                }
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
