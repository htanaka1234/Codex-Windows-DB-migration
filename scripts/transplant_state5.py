import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LIVE = Path(os.environ.get("CODEX_STATE_DB", Path.home() / ".codex" / "state_5.sqlite"))
SOURCE = ROOT / "state_5.sqlite.orig-20260521-194352"
STAMP = datetime.now().strftime("%Y%m%d-%H%M%S")
OUT = ROOT / f"state_5.transplanted-{STAMP}.sqlite"

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

SKIP_TABLES = {"_sqlx_migrations", "sqlite_sequence", "backfill_state", "jobs"}


def connect(path: Path, readonly: bool = False) -> sqlite3.Connection:
    if readonly:
        return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    return sqlite3.connect(path)


def quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def table_columns(con: sqlite3.Connection, db: str, table: str) -> list[str]:
    rows = con.execute(f"pragma {db}.table_info({quote(table)})").fetchall()
    return [row[1] for row in rows]


def table_count(con: sqlite3.Connection, db: str, table: str) -> int:
    return con.execute(f"select count(*) from {db}.{quote(table)}").fetchone()[0]


def open_snapshot(live: Path, out: Path) -> None:
    if out.exists():
        raise FileExistsError(out)
    src = connect(live, readonly=True)
    try:
        dst = connect(out)
        try:
            src.backup(dst)
            dst.execute("pragma wal_checkpoint(truncate)")
            dst.commit()
        finally:
            dst.close()
    finally:
        src.close()


def path_summary(path: Path) -> dict:
    con = connect(path, readonly=True)
    try:
        rows = con.execute(
            "select rollout_path, count(*) from threads group by rollout_path order by count(*) desc, rollout_path limit 12"
        ).fetchall()
        cwd_rows = con.execute(
            "select cwd, count(*) from threads group by cwd order by count(*) desc, cwd limit 12"
        ).fetchall()
        minmax = con.execute(
            "select count(*), min(created_at), max(updated_at), min(created_at_ms), max(updated_at_ms) from threads"
        ).fetchone()
        return {
            "path": str(path),
            "threads": minmax[0],
            "created_at_min": minmax[1],
            "updated_at_max": minmax[2],
            "created_at_ms_min": minmax[3],
            "updated_at_ms_max": minmax[4],
            "rollout_paths": rows,
            "cwd": cwd_rows,
        }
    finally:
        con.close()


def analyze() -> None:
    live = connect(LIVE, readonly=True)
    source = connect(SOURCE, readonly=True)
    try:
        print(json.dumps({"live": path_summary(LIVE), "source": path_summary(SOURCE)}, ensure_ascii=False, indent=2))
        live_ids = {row[0] for row in live.execute("select id from threads")}
        source_ids = {row[0] for row in source.execute("select id from threads")}
        print(json.dumps({
            "source_threads": len(source_ids),
            "live_threads": len(live_ids),
            "already_in_live": len(source_ids & live_ids),
            "missing_from_live": len(source_ids - live_ids),
            "extra_in_live": len(live_ids - source_ids),
        }, ensure_ascii=False, indent=2))
        for table in TABLES:
            if table_columns(live, "main", table) and table_columns(source, "main", table):
                print(json.dumps({
                    "table": table,
                    "live_count": table_count(live, "main", table),
                    "source_count": table_count(source, "main", table),
                }, ensure_ascii=False))
    finally:
        live.close()
        source.close()


def merge_into(candidate: Path, vacuum: bool = True) -> dict:
    con = connect(candidate)
    try:
        con.execute("pragma foreign_keys=off")
        con.execute("attach database ? as src", (str(SOURCE),))
        before = {}
        after = {}
        inserted = {}
        for table in TABLES:
            dst_cols = table_columns(con, "main", table)
            src_cols = table_columns(con, "src", table)
            if not dst_cols or not src_cols:
                continue
            common = [col for col in dst_cols if col in src_cols]
            if not common:
                continue
            before[table] = table_count(con, "main", table)
            src_count = table_count(con, "src", table)
            cols_sql = ", ".join(quote(col) for col in common)
            select_sql = ", ".join(f"src.{quote(table)}.{quote(col)}" for col in common)
            con.execute(
                f"insert or ignore into main.{quote(table)} ({cols_sql}) "
                f"select {select_sql} from src.{quote(table)}"
            )
            after[table] = table_count(con, "main", table)
            inserted[table] = after[table] - before[table]
            print(json.dumps({
                "table": table,
                "source": src_count,
                "before": before[table],
                "after": after[table],
                "inserted": inserted[table],
            }, ensure_ascii=False))

        con.commit()
        con.execute("detach database src")
        con.execute("pragma foreign_keys=on")
        fk = con.execute("pragma foreign_key_check").fetchall()
        quick = [row[0] for row in con.execute("pragma quick_check")]
        integrity = [row[0] for row in con.execute("pragma integrity_check")]
        con.commit()
        if vacuum:
            con.execute("vacuum")
            con.commit()
        return {
            "candidate": str(candidate),
            "inserted": inserted,
            "foreign_key_check": fk[:10],
            "foreign_key_error_count": len(fk),
            "quick_check": quick,
            "integrity_check": integrity,
        }
    finally:
        con.close()


def create_candidate() -> None:
    open_snapshot(LIVE, OUT)
    summary = merge_into(OUT)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def merge_live() -> None:
    backup = ROOT / f"state_5.live-before-transplant-{STAMP}.sqlite"
    open_snapshot(LIVE, backup)
    summary = merge_into(LIVE, vacuum=False)
    summary["live_backup"] = str(backup)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def install(candidate: Path) -> None:
    if not candidate.exists():
        raise FileNotFoundError(candidate)
    backup_dir = LIVE.parent / f"pre-transplant-state_5-{STAMP}"
    backup_dir.mkdir(exist_ok=False)
    for path in [LIVE, LIVE.with_name("state_5.sqlite-wal"), LIVE.with_name("state_5.sqlite-shm")]:
        if path.exists():
            shutil.move(str(path), str(backup_dir / path.name))
    shutil.copy2(candidate, LIVE)
    print(json.dumps({"installed": str(LIVE), "backup_dir": str(backup_dir)}, ensure_ascii=False, indent=2))


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "analyze"
    if cmd == "analyze":
        analyze()
    elif cmd == "create":
        create_candidate()
    elif cmd == "merge-live":
        merge_live()
    elif cmd == "install":
        install(Path(sys.argv[2]).resolve())
    else:
        print("usage: transplant_state5.py [analyze|create|merge-live|install CANDIDATE]")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
