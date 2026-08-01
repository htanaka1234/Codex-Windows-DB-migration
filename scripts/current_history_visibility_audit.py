import json
import os
import sqlite3
import sys
from pathlib import Path

from codex_agent_config import audit_agent_config
from codex_path_styles import (
    PathConversionError,
    strip_long_windows_prefix,
    to_windows_path,
    to_wsl_path,
    windows_path_problem,
)


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


def norm_windows(path: str | None, wsl_distro: str | None = None) -> str:
    if not path:
        return ""
    return to_windows_path(path, long_prefix=False, wsl_distro=wsl_distro)


def norm_wsl(path: str | None) -> str:
    if not path:
        return ""
    return to_wsl_path(path)


def norm_root(path: str | None, target_style: str, wsl_distro: str | None = None) -> str:
    if target_style == "wsl":
        return norm_wsl(path)
    if target_style == "windows":
        return norm_windows(path, wsl_distro)
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


def path_audit(value: str, target_style: str, wsl_distro: str | None = None):
    result = {"value": value}
    if target_style == "windows":
        result["problem"] = windows_path_problem(value)
    try:
        result["normalized_for_target"] = norm_root(value, target_style, wsl_distro)
    except PathConversionError as exc:
        result["normalized_for_target"] = None
        result["problem"] = str(exc)
    return result


def inspect_state_db(state_db: Path, target_style: str, wsl_distro: str | None = None):
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
        problems = []
        for row in by_cwd:
            audit = path_audit(row["cwd"], target_style, wsl_distro)
            row["normalized_for_target"] = audit["normalized_for_target"]
            row["path_problem"] = audit.get("problem")
            if audit.get("problem"):
                problems.append({"source": "threads.cwd", "cwd": row["cwd"], "problem": audit["problem"]})
        print_json("state_by_cwd", by_cwd)
        live_rows = rows(
            con,
            "select id, cwd, source, thread_source, archived, preview from threads",
        )
        return live_rows, problems


def inspect_global_state(global_state: Path, live_rows, target_style: str, wsl_distro: str | None = None):
    if not global_state.exists():
        print_json("global_state", {"path": str(global_state), "exists": False})
        return []
    state = load_json(global_state)
    saved = []
    problems = []
    for key in ("active-workspace-roots", "electron-saved-workspace-roots", "project-order"):
        for value in state.get(key, []):
            audit = path_audit(value, target_style, wsl_distro)
            saved.append({
                "source": key,
                "root": value,
                "normalized": audit["normalized_for_target"],
                "path_problem": audit.get("problem"),
            })
            if audit.get("problem"):
                problems.append({"source": key, "value": value, "problem": audit["problem"]})
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
        normalized = 0
        if item["normalized"] is not None:
            for row in live_rows:
                try:
                    row_normalized = norm_root(row["cwd"], target_style, wsl_distro)
                except PathConversionError:
                    continue
                if (
                    row_normalized.lower() == item["normalized"].lower()
                    and row["source"] == "vscode"
                    and row["thread_source"] == "user"
                    and row["archived"] == 0
                    and row["preview"] != ""
                ):
                    normalized += 1
        counts.append({**item, "exact_visible_user_like": exact, "normalized_visible_user_like": normalized})
    print_json("workspace_root_match_counts", counts)
    hints = state.get("thread-workspace-root-hints", {})
    hint_audits = []
    for thread_id, value in hints.items():
        if not isinstance(value, str):
            continue
        audit = path_audit(value, target_style, wsl_distro)
        if audit.get("problem"):
            problem = {"source": "thread-workspace-root-hints", "thread_id": thread_id, "value": value, "problem": audit["problem"]}
            problems.append(problem)
            hint_audits.append(problem)
    print_json("thread_workspace_root_hints", {
        "count": len(hints),
        "sample": dict(list(hints.items())[:10]),
        "path_problems": hint_audits[:20],
    })
    return problems


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
    wsl_distro = os.environ.get("WSL_DISTRO_NAME")

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
        elif arg == "--wsl-distro":
            i += 1
            if i >= len(args):
                raise SystemExit("--wsl-distro requires a value")
            wsl_distro = args[i]
        else:
            raise SystemExit(
                "usage: current_history_visibility_audit.py "
                "[--target-style windows|wsl|auto] [--wsl-distro NAME] "
                "[--codex-home PATH] [--source-db PATH]"
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
    config_path = codex_home / "config.toml"

    live_rows, path_problems = inspect_state_db(state_db, target_style, wsl_distro)
    path_problems.extend(inspect_global_state(global_state, live_rows, target_style, wsl_distro))
    inspect_session_index(session_index, source_db, live_rows)
    inspect_logs_db(log_db)
    config_audit = audit_agent_config(config_path, target_style, wsl_distro)
    print_json("agent_config_paths", config_audit)
    path_problems.extend(config_audit["problems"])
    print_json("path_validation", {
        "target_style": target_style,
        "wsl_distro": wsl_distro,
        "problem_count": len(path_problems),
        "problems": path_problems[:50],
    })
    return 2 if path_problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
