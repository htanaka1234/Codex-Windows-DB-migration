import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from codex_path_styles import PathConversionError, strip_long_windows_prefix, to_windows_path, to_wsl_path


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


def default_backup_root() -> Path:
    if os.environ.get("RECOVERY_WORKDIR"):
        return Path(os.environ["RECOVERY_WORKDIR"])
    if os.environ.get("CODEX_RECOVERY_WORKDIR"):
        return Path(os.environ["CODEX_RECOVERY_WORKDIR"])
    return Path.cwd()


def default_target_style() -> str:
    if os.name == "nt":
        return "windows"
    if Path("/mnt/c").exists():
        return "wsl"
    return "windows"


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


def iso_from_ms(ms):
    if ms is None:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def load_index(session_index: Path):
    rows = []
    if session_index.exists():
        with session_index.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    return rows


def load_threads(db: Path):
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in con.execute(
            "select id,title,updated_at_ms,cwd from threads "
            "where source='vscode' and thread_source='user' and archived=0 and preview<>'' "
            "order by updated_at_ms asc, id asc"
        )]
    finally:
        con.close()


def write_index(session_index: Path, rows):
    tmp = session_index.with_suffix(".jsonl.tmp-codex-restore")
    with tmp.open("w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    tmp.replace(session_index)


def main() -> int:
    args = sys.argv[1:]
    mode = "dry-run"
    target_style = default_target_style()
    codex_home = default_codex_home()
    backup_root = default_backup_root()
    wsl_distro = os.environ.get("WSL_DISTRO_NAME")

    i = 0
    while i < len(args):
        arg = args[i]
        if arg in {"dry-run", "apply"}:
            mode = arg
        elif arg == "--target-style":
            i += 1
            if i >= len(args):
                raise SystemExit("--target-style requires a value")
            target_style = args[i]
        elif arg == "--codex-home":
            i += 1
            if i >= len(args):
                raise SystemExit("--codex-home requires a value")
            codex_home = Path(args[i])
        elif arg == "--backup-root":
            i += 1
            if i >= len(args):
                raise SystemExit("--backup-root requires a value")
            backup_root = Path(args[i])
        elif arg == "--wsl-distro":
            i += 1
            if i >= len(args):
                raise SystemExit("--wsl-distro requires a value")
            wsl_distro = args[i]
        else:
            raise SystemExit(
                "usage: repair_ui_indexes.py [dry-run|apply] "
                "[--target-style windows|wsl|auto] [--wsl-distro NAME] "
                "[--codex-home PATH] [--backup-root PATH]"
            )
        i += 1

    if mode not in {"dry-run", "apply"}:
        raise SystemExit("usage: repair_ui_indexes.py [dry-run|apply]")
    if target_style == "auto":
        target_style = default_target_style()
    if target_style not in {"windows", "wsl"}:
        raise SystemExit("--target-style must be windows, wsl, or auto")

    db = codex_home / "state_5.sqlite"
    session_index = codex_home / "session_index.jsonl"
    global_state = codex_home / ".codex-global-state.json"

    threads = load_threads(db)
    old_index = load_index(session_index)
    old_by_id = {row["id"]: row for row in old_index if "id" in row}

    new_index = []
    for thread in threads:
        existing = old_by_id.get(thread["id"], {})
        new_index.append({
            "id": thread["id"],
            "thread_name": existing.get("thread_name") or (thread["title"] or thread["id"])[:120],
            "updated_at": existing.get("updated_at") or iso_from_ms(thread["updated_at_ms"]),
        })

    if global_state.exists():
        state = json.loads(global_state.read_text(encoding="utf-8"))
    else:
        state = {}
    new_state = json.loads(json.dumps(state))
    conversion_errors = []
    for key in ("active-workspace-roots", "electron-saved-workspace-roots", "project-order"):
        if isinstance(new_state.get(key), list):
            seen = set()
            values = []
            for item in new_state[key]:
                try:
                    value = norm_root(item, target_style, wsl_distro) if isinstance(item, str) else item
                except PathConversionError as exc:
                    conversion_errors.append({"source": key, "value": item, "error": str(exc)})
                    value = item
                dedupe = value.lower() if isinstance(value, str) else repr(value)
                if dedupe not in seen:
                    seen.add(dedupe)
                    values.append(value)
            new_state[key] = values

    hints = dict(new_state.get("thread-workspace-root-hints", {}))
    for thread_id, value in list(hints.items()):
        if not isinstance(value, str):
            continue
        try:
            hints[thread_id] = norm_root(value, target_style, wsl_distro)
        except PathConversionError as exc:
            conversion_errors.append({
                "source": "thread-workspace-root-hints",
                "thread_id": thread_id,
                "value": value,
                "error": str(exc),
            })
    for thread in threads:
        try:
            hints[thread["id"]] = norm_root(thread["cwd"], target_style, wsl_distro)
        except PathConversionError as exc:
            conversion_errors.append({
                "source": "threads.cwd",
                "thread_id": thread["id"],
                "value": thread["cwd"],
                "error": str(exc),
            })
    new_state["thread-workspace-root-hints"] = hints

    diff = {
        "mode": mode,
        "target_style": target_style,
        "wsl_distro": wsl_distro,
        "codex_home": str(codex_home),
        "session_index_old_rows": len(old_index),
        "session_index_new_rows": len(new_index),
        "session_index_added": sorted(set(row["id"] for row in new_index) - set(old_by_id))[:20],
        "global_state_project_order_old": state.get("project-order"),
        "global_state_project_order_new": new_state.get("project-order"),
        "thread_workspace_root_hints_old_count": len(state.get("thread-workspace-root-hints", {})),
        "thread_workspace_root_hints_new_count": len(hints),
        "cwd_conversion_errors": conversion_errors[:20],
    }
    print(json.dumps({"plan": diff}, ensure_ascii=False, indent=2))

    if conversion_errors:
        print("Refusing to continue because one or more workspace roots cannot be converted safely.", file=sys.stderr)
        return 2

    if mode == "dry-run":
        return 0

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    index_backup = backup_root / f"session_index.before-ui-index-repair-{target_style}-{ts}.jsonl"
    global_backup = backup_root / f"codex-global-state.before-ui-index-repair-{target_style}-{ts}.json"
    shutil.copy2(session_index, index_backup)
    shutil.copy2(global_state, global_backup)

    write_index(session_index, new_index)
    tmp_state = global_state.with_suffix(".json.tmp-codex-restore")
    tmp_state.write_text(json.dumps(new_state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp_state.replace(global_state)

    print(json.dumps({
        "applied": {
            "session_index_backup": str(index_backup),
            "global_state_backup": str(global_backup),
            "session_index_rows": len(new_index),
            "thread_workspace_root_hints": len(hints),
        }
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
