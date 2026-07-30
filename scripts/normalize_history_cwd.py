import json
import os
import re
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path


MNT_RE = re.compile(r"^/mnt/([A-Za-z])(?:/(.*))?$")
DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")
DRIVE_SPLIT_RE = re.compile(r"^([A-Za-z]):(?:[\\/](.*))?$")


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


def default_backup_dir() -> Path:
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


def strip_long_windows_prefix(value: str) -> str:
    if value.startswith("\\\\?\\"):
        return value[4:]
    return value


def to_wsl_cwd(value: str) -> str:
    value = strip_long_windows_prefix(value or "")
    normalized = value.replace("\\", "/")
    m = MNT_RE.match(normalized)
    if m:
        drive = m.group(1).lower()
        rest = (m.group(2) or "").replace("\\", "/").strip("/")
        return f"/mnt/{drive}/{rest}" if rest else f"/mnt/{drive}"

    m = DRIVE_SPLIT_RE.match(value)
    if not m:
        return normalized.rstrip("/")
    drive = m.group(1).lower()
    rest = (m.group(2) or "").replace("\\", "/").strip("/")
    return f"/mnt/{drive}/{rest}" if rest else f"/mnt/{drive}"


def to_windows_cwd(value: str, *, long_prefix: bool) -> str:
    if not value:
        return value
    if value.startswith("\\\\?\\"):
        base = value.replace("/", "\\")
        return base if long_prefix else base[4:].rstrip("\\")
    m = MNT_RE.match(value)
    if m:
        drive = m.group(1).upper()
        rest = (m.group(2) or "").replace("/", "\\")
        if rest:
            base = f"{drive}:\\{rest}"
        else:
            base = f"{drive}:\\"
        return f"\\\\?\\{base}" if long_prefix else base.rstrip("\\")
    if DRIVE_RE.match(value):
        base = value.replace("/", "\\")
        return f"\\\\?\\{base}" if long_prefix and not base.startswith("\\\\?\\") else base.rstrip("\\")
    return value.replace("/", "\\") if long_prefix else value.replace("/", "\\").rstrip("\\")


def canonical_cwd(value: str, target_style: str) -> str:
    if target_style == "wsl":
        return to_wsl_cwd(value)
    if target_style == "windows":
        return to_windows_cwd(value, long_prefix=True)
    raise ValueError(f"unsupported target style: {target_style}")


def rows(con, sql, args=()):
    con.row_factory = sqlite3.Row
    return [dict(row) for row in con.execute(sql, args)]


def print_json(name, value):
    print(json.dumps({name: value}, ensure_ascii=False, indent=2, default=str))


def backup_db(con: sqlite3.Connection, backup_dir: Path, target_style: str) -> Path:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = backup_dir / f"state_5.live-before-cwd-normalize-{target_style}-{ts}.sqlite"
    dst = sqlite3.connect(str(backup_path))
    try:
        con.backup(dst)
    finally:
        dst.close()
    return backup_path


def analyze(con: sqlite3.Connection, target_style: str):
    candidates = rows(
        con,
        "select id,cwd,source,thread_source,archived,substr(title,1,80) title "
        "from threads order by updated_at_ms desc, id desc",
    )
    cwd_updates = []
    for row in candidates:
        new = canonical_cwd(row["cwd"], target_style)
        if new != row["cwd"]:
            cwd_updates.append({**row, "new_cwd": new})
    source_updates = rows(
        con,
        "select id,cwd,substr(title,1,80) title from threads "
        "where source='vscode' and thread_source is null and archived=0 "
        "and preview<>'' and first_user_message<>'' "
        "order by updated_at_ms desc, id desc",
    )
    return cwd_updates, source_updates


def summarize(con: sqlite3.Connection):
    print_json("integrity", {
        "integrity_check": con.execute("pragma integrity_check").fetchone()[0],
        "quick_check": con.execute("pragma quick_check").fetchone()[0],
    })
    print_json("by_cwd", rows(
        con,
        "select cwd, count(*) total, "
        "sum(case when source='vscode' and thread_source='user' and archived=0 and preview<>'' then 1 else 0 end) visible_user_like "
        "from threads group by cwd order by total desc, cwd",
    ))
    print_json("by_source_thread_source", rows(
        con,
        "select source, thread_source, count(*) count from threads "
        "group by source, thread_source order by count desc, source, thread_source",
    ))


def main() -> int:
    args = sys.argv[1:]
    mode = "dry-run"
    target_style = default_target_style()
    codex_home = default_codex_home()
    backup_dir = default_backup_dir()

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
            backup_dir = Path(args[i])
        else:
            raise SystemExit(
                "usage: normalize_history_cwd.py [dry-run|apply] "
                "[--target-style windows|wsl|auto] [--codex-home PATH] [--backup-root PATH]"
            )
        i += 1

    if mode not in {"dry-run", "apply"}:
        raise SystemExit("usage: normalize_history_cwd.py [dry-run|apply]")
    if target_style == "auto":
        target_style = default_target_style()
    if target_style not in {"windows", "wsl"}:
        raise SystemExit("--target-style must be windows, wsl, or auto")

    db = codex_home / "state_5.sqlite"

    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    try:
        cwd_updates, source_updates = analyze(con, target_style)
        print_json("plan", {
            "mode": mode,
            "target_style": target_style,
            "db": str(db),
            "cwd_rows_to_update": len(cwd_updates),
            "thread_source_rows_to_update": len(source_updates),
            "cwd_update_sample": cwd_updates[:12],
            "thread_source_update_sample": source_updates[:12],
        })
        if mode == "dry-run":
            summarize(con)
            return 0

        if not cwd_updates and not source_updates:
            print_json("no_changes", {
                "message": "state_5.sqlite is already normalized for the requested target style",
                "target_style": target_style,
                "db": str(db),
                "backup_created": False,
                "cwd_rows_updated": 0,
                "thread_source_rows_updated": 0,
            })
            summarize(con)
            return 0

        backup_path = backup_db(con, backup_dir, target_style)
        with con:
            for row in cwd_updates:
                con.execute(
                    "update threads set cwd=? where id=?",
                    (row["new_cwd"], row["id"]),
                )
            con.execute(
                "update threads set thread_source='user' "
                "where source='vscode' and thread_source is null and archived=0 "
                "and preview<>'' and first_user_message<>''",
            )
        print_json("applied", {
            "backup": str(backup_path),
            "cwd_rows_updated": len(cwd_updates),
            "thread_source_rows_updated": len(source_updates),
        })
        summarize(con)
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
