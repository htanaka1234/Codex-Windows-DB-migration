from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from pathlib import Path


LIVE_DB = Path(os.environ.get("CODEX_STATE_DB", Path.home() / ".codex" / "state_5.sqlite"))
WORKSPACE = Path(os.environ.get("CODEX_RECOVERY_WORKDIR", Path.cwd()))


def connect(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    return con


def rows(con: sqlite3.Connection, sql: str, args: tuple = ()) -> list[dict]:
    return [dict(row) for row in con.execute(sql, args)]


def summary(con: sqlite3.Connection) -> dict:
    return {
        "integrity_check": con.execute("pragma integrity_check").fetchone()[0],
        "quick_check": con.execute("pragma quick_check").fetchone()[0],
        "threads": con.execute("select count(*) from threads").fetchone()[0],
        "by_source_thread_source": rows(
            con,
            """
            select source, thread_source, count(*) as count
            from threads
            group by source, thread_source
            order by count desc, source, thread_source
            """,
        ),
        "candidate_rows": con.execute(
            """
            select count(*)
            from threads
            where source = 'vscode'
              and thread_source is null
              and archived = 0
              and preview <> ''
              and first_user_message <> ''
            """
        ).fetchone()[0],
    }


def backup_live(src: Path) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dst = WORKSPACE / f"state_5.live-before-thread-source-fix-{stamp}.sqlite"
    with connect(src) as source, sqlite3.connect(dst) as target:
        source.backup(target)
    return dst


def apply_fix() -> dict:
    backup = backup_live(LIVE_DB)
    with connect(LIVE_DB) as con:
        before = summary(con)
        con.execute("begin immediate")
        try:
            updated = con.execute(
                """
                update threads
                set thread_source = 'user'
                where source = 'vscode'
                  and thread_source is null
                  and archived = 0
                  and preview <> ''
                  and first_user_message <> ''
                """
            ).rowcount
            con.commit()
        except Exception:
            con.rollback()
            raise
        after = summary(con)
        foreign_key_errors = rows(con, "pragma foreign_key_check")
    return {
        "live_db": str(LIVE_DB),
        "backup": str(backup),
        "updated_rows": updated,
        "before": before,
        "after": after,
        "foreign_key_check": foreign_key_errors,
    }


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "dry-run"
    if mode == "dry-run":
        with connect(LIVE_DB) as con:
            print(json.dumps(summary(con), ensure_ascii=False, indent=2))
        return 0
    if mode == "apply":
        print(json.dumps(apply_fix(), ensure_ascii=False, indent=2))
        return 0
    print("usage: restore_thread_visibility.py [dry-run|apply]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
