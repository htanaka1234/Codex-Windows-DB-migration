import json
import os
import re
import sqlite3
import sys
from pathlib import Path


MNT_RE = re.compile(r"^/mnt/([A-Za-z])(?:/(.*))?$")
DRIVE_RE = re.compile(r"^([A-Za-z]):(?:[\\/](.*))?$")


def looks_like_codex_home(path: Path) -> bool:
    return (path / "state_5.sqlite").exists() and (path / "sessions").is_dir()


def default_codex_home() -> Path:
    if os.environ.get("CODEX_HOME"):
        return Path(os.environ["CODEX_HOME"])
    userprofile = os.environ.get("USERPROFILE")
    if userprofile:
        candidate = Path(userprofile) / ".codex"
        if looks_like_codex_home(candidate):
            return candidate
    for candidate in sorted(Path("/mnt/c/Users").glob("*/.codex")):
        if looks_like_codex_home(candidate):
            return candidate
    home_candidate = Path.home() / ".codex"
    if looks_like_codex_home(home_candidate):
        return home_candidate
    cwd = Path.cwd()
    if looks_like_codex_home(cwd):
        return cwd
    return Path.home() / ".codex"


def default_source_db() -> Path | None:
    candidate = Path.cwd() / "state_5.sqlite.orig-20260521-194352"
    if candidate.exists():
        return candidate
    return None


def default_target_style() -> str:
    if os.name == "nt":
        return "windows"
    if Path("/mnt/c").exists():
        return "wsl"
    return "windows"


def connect_ro(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def rows(con: sqlite3.Connection, sql: str, args=()):
    return [dict(row) for row in con.execute(sql, args)]


def one(con: sqlite3.Connection, sql: str, args=()):
    row = con.execute(sql, args).fetchone()
    if row is None:
        return None
    if len(row) == 1:
        return row[0]
    return dict(row)


def strip_long_windows_prefix(value: str) -> str:
    if value.startswith("\\\\?\\"):
        return value[4:]
    return value


def norm_windows(path: str | None) -> str:
    if not path:
        return ""
    p = strip_long_windows_prefix(path).replace("/", "\\")
    m = MNT_RE.match(path.replace("\\", "/"))
    if m:
        drive = m.group(1).upper()
        rest = (m.group(2) or "").replace("/", "\\").rstrip("\\")
        return f"{drive}:\\{rest}" if rest else f"{drive}:\\"
    m = DRIVE_RE.match(p)
    if m:
        drive = m.group(1).upper()
        rest = (m.group(2) or "").replace("/", "\\").rstrip("\\")
        return f"{drive}:\\{rest}" if rest else f"{drive}:\\"
    return p.rstrip("\\")


def norm_wsl(path: str | None) -> str:
    if not path:
        return ""
    p = strip_long_windows_prefix(path)
    normalized = p.replace("\\", "/")
    m = MNT_RE.match(normalized)
    if m:
        drive = m.group(1).lower()
        rest = (m.group(2) or "").strip("/")
        return f"/mnt/{drive}/{rest}" if rest else f"/mnt/{drive}"
    m = DRIVE_RE.match(p)
    if m:
        drive = m.group(1).lower()
        rest = (m.group(2) or "").replace("\\", "/").strip("/")
        return f"/mnt/{drive}/{rest}" if rest else f"/mnt/{drive}"
    return normalized.rstrip("/")


def norm_root(path: str | None, target_style: str) -> str:
    if target_style == "wsl":
        return norm_wsl(path)
    if target_style == "windows":
        return norm_windows(path)
    raise ValueError(f"unsupported target style: {target_style}")


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def load_session_index(path: Path):
    out = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def print_json(name, value):
    print(json.dumps({name: value}, ensure_ascii=False, indent=2, default=str))


def inspect_state_db(state_db: Path, target_style: str):
    with connect_ro(state_db) as con:
        print_json("state_db", {
            "path": str(state_db),
            "target_style": target_style,
            "integrity_check": one(con, "pragma integrity_check"),
            "quick_check": one(con, "pragma quick_check"),
            "threads": one(con, "select count(*) from threads"),
            "visible_user_like": one(
                con,
                "select count(*) from threads where archived=0 and source='vscode' and thread_source='user' and preview<>''",
            ),
        })
        print_json("state_by_source_thread_source", rows(
            con,
            "select source, thread_source, count(*) count from threads "
            "group by source, thread_source order by count desc, source, thread_source",
        ))
        print_json("vscode_thread_source_null_rows", rows(
            con,
            "select id, updated_at_ms, cwd, substr(title,1,90) title, rollout_path "
            "from threads where source='vscode' and thread_source is null "
            "order by updated_at_ms desc, id desc",
        ))
        by_cwd = rows(
            con,
            "select cwd, count(*) total, "
            "sum(case when source='vscode' and thread_source='user' and archived=0 then 1 else 0 end) visible_user_like "
            "from threads group by cwd order by total desc, cwd",
        )
        for row in by_cwd:
            row["normalized_for_target"] = norm_root(row["cwd"], target_style)
        print_json("state_by_cwd", by_cwd)
        return rows(
            con,
            "select id, cwd, source, thread_source, archived, preview from threads",
        )


def inspect_global_state(global_state: Path, live_rows, target_style: str):
    if not global_state.exists():
        print_json("global_state", {"path": str(global_state), "exists": False})
        return
    state = load_json(global_state)
    saved = []
    for key in ("active-workspace-roots", "electron-saved-workspace-roots", "project-order"):
        for value in state.get(key, []):
            saved.append({"source": key, "root": value, "normalized": norm_root(value, target_style)})
    counts = []
    for item in saved:
        exact = sum(
            1
            for row in live_rows
            if row["cwd"] == item["root"]
            and row["source"] == "vscode"
            and row["thread_source"] == "user"
            and row["archived"] == 0
            and row["preview"] != ""
        )
        normalized = sum(
            1
            for row in live_rows
            if norm_root(row["cwd"], target_style).lower() == item["normalized"].lower()
            and row["source"] == "vscode"
            and row["thread_source"] == "user"
            and row["archived"] == 0
            and row["preview"] != ""
        )
        counts.append({**item, "exact_visible_user_like": exact, "normalized_visible_user_like": normalized})
    print_json("workspace_root_match_counts", counts)
    print_json("thread_workspace_root_hints", {
        "count": len(state.get("thread-workspace-root-hints", {})),
        "sample": dict(list(state.get("thread-workspace-root-hints", {}).items())[:10]),
    })


def inspect_session_index(session_index: Path, source_db: Path | None, live_rows):
    index_rows = load_session_index(session_index)
    index_ids = {row["id"] for row in index_rows}
    live_ids = {row["id"] for row in live_rows}
    live_user_ids = {
        row["id"]
        for row in live_rows
        if row["source"] == "vscode" and row["thread_source"] == "user" and row["archived"] == 0 and row["preview"] != ""
    }
    source_ids = set()
    if source_db and source_db.exists():
        with connect_ro(source_db) as con:
            source_ids = {row["id"] for row in con.execute("select id from threads")}
    print_json("session_index_compare", {
        "path": str(session_index),
        "rows": len(index_rows),
        "ids_in_live": len(index_ids & live_ids),
        "ids_missing_from_live": len(index_ids - live_ids),
        "live_visible_user_ids": len(live_user_ids),
        "live_visible_user_ids_in_index": len(live_user_ids & index_ids),
        "live_visible_user_ids_missing_from_index": len(live_user_ids - index_ids),
        "source_ids": len(source_ids),
        "source_ids_in_index": len(source_ids & index_ids),
        "source_ids_missing_from_index": len(source_ids - index_ids),
        "tail": index_rows[-8:],
    })
    if source_ids:
        missing = sorted(source_ids - index_ids)
        print_json("source_ids_missing_from_session_index_sample", missing[:15])


def inspect_logs_db(log_db: Path):
    if not log_db.exists():
        print_json("logs_db", {"exists": False})
        return
    with connect_ro(log_db) as con:
        tables = rows(con, "select name, sql from sqlite_master where type='table' order by name")
        print_json("logs_db_tables", [{"name": row["name"], "sql": row["sql"][:400]} for row in tables])
        for table in tables:
            name = table["name"]
            try:
                cols = rows(con, f"pragma table_info({name})")
                count = one(con, f"select count(*) from {name}")
                print_json(f"logs_db_table_{name}", {"count": count, "columns": cols})
            except sqlite3.Error as exc:
                print_json(f"logs_db_table_{name}_error", str(exc))


def main() -> int:
    args = sys.argv[1:]
    target_style = default_target_style()
    codex_home = default_codex_home()
    source_db = default_source_db()

    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--target-style":
            i += 1
            if i >= len(args):
                raise SystemExit("--target-style requires a value")
            target_style = args[i]
        elif arg == "--codex-home":
            i += 1
            if i >= len(args):
                raise SystemExit("--codex-home requires a value")
            codex_home = Path(args[i])
        elif arg == "--source-db":
            i += 1
            if i >= len(args):
                raise SystemExit("--source-db requires a value")
            source_db = Path(args[i])
        else:
            raise SystemExit(
                "usage: current_history_visibility_audit.py "
                "[--target-style windows|wsl|auto] [--codex-home PATH] [--source-db PATH]"
            )
        i += 1

    if target_style == "auto":
        target_style = default_target_style()
    if target_style not in {"windows", "wsl"}:
        raise SystemExit("--target-style must be windows, wsl, or auto")

    state_db = codex_home / "state_5.sqlite"
    log_db = codex_home / "logs_2.sqlite"
    session_index = codex_home / "session_index.jsonl"
    global_state = codex_home / ".codex-global-state.json"

    live_rows = inspect_state_db(state_db, target_style)
    inspect_global_state(global_state, live_rows, target_style)
    inspect_session_index(session_index, source_db, live_rows)
    inspect_logs_db(log_db)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
