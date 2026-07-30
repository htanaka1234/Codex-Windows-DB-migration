import json
import os
import sqlite3
import sys
from pathlib import Path


DB = (
    Path(sys.argv[1]).resolve()
    if len(sys.argv) > 1
    else Path(os.environ.get("CODEX_STATE_DB", Path.home() / ".codex" / "state_5.sqlite"))
)


def rows(con, sql, args=()):
    return [dict(row) for row in con.execute(sql, args)]


def main() -> int:
    con = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        columns = [row["name"] for row in con.execute("pragma table_info(threads)")]
        print(json.dumps({"columns": columns}, ensure_ascii=True, indent=2))
        queries = {
            "by_archived": "select archived, count(*) count from threads group by archived order by archived",
            "by_source": "select source, count(*) count from threads group by source order by count desc, source",
            "by_thread_source": "select thread_source, count(*) count from threads group by thread_source order by count desc, thread_source",
            "by_source_thread_source": (
                "select source, thread_source, count(*) count from threads "
                "group by source, thread_source order by count desc, source, thread_source"
            ),
            "by_has_user_event": "select has_user_event, count(*) count from threads group by has_user_event order by has_user_event",
            "by_cwd": "select cwd, count(*) count from threads group by cwd order by count desc, cwd",
            "by_source_archived": "select source, archived, count(*) count from threads group by source, archived order by source, archived",
            "empty_text": (
                "select "
                "sum(case when title='' then 1 else 0 end) empty_title, "
                "sum(case when first_user_message='' then 1 else 0 end) empty_first_user_message, "
                "sum(case when preview='' then 1 else 0 end) empty_preview, "
                "count(*) count from threads"
            ),
            "visible_like_no_cwd": (
                "select count(*) count from threads "
                "where archived=0 and has_user_event=1"
            ),
        }
        for name, sql in queries.items():
            print(json.dumps({name: rows(con, sql)}, ensure_ascii=True, indent=2))

        print(json.dumps(
            {
                "recent_threads": rows(
                    con,
                    "select id,substr(title,1,80) title,substr(preview,1,80) preview,"
                    "substr(first_user_message,1,80) first_user_message,cwd,source,thread_source,archived,has_user_event,"
                    "created_at,updated_at,created_at_ms,updated_at_ms,rollout_path "
                    "from threads order by updated_at_ms desc, id desc limit 8",
                )
            },
            ensure_ascii=True,
            indent=2,
        ))

        rollout_paths = rows(
            con,
            "select id,rollout_path from threads order by updated_at_ms desc, id desc",
        )
        missing = []
        mismatched = []
        for row in rollout_paths:
            path = Path(row["rollout_path"])
            if not path.exists():
                missing.append({"id": row["id"], "rollout_path": row["rollout_path"]})
                continue
            try:
                first_line = path.read_text(encoding="utf-8", errors="replace").splitlines()[0]
                meta = json.loads(first_line)
                file_id = meta.get("id") or meta.get("session_meta", {}).get("id")
                if file_id and file_id != row["id"]:
                    mismatched.append({"id": row["id"], "file_id": file_id, "rollout_path": row["rollout_path"]})
            except Exception as exc:
                mismatched.append({"id": row["id"], "error": str(exc), "rollout_path": row["rollout_path"]})
        print(json.dumps({"missing_rollout_files": missing}, ensure_ascii=True, indent=2))
        print(json.dumps({"mismatched_rollout_files": mismatched}, ensure_ascii=True, indent=2))

        for cwd in [r["cwd"] for r in rows(con, "select cwd from threads group by cwd order by count(*) desc")]:
            count = con.execute(
                "select count(*) from threads where cwd=? and archived=0 and has_user_event=1",
                (cwd,),
            ).fetchone()[0]
            print(json.dumps({"visible_like_by_cwd": {"cwd": cwd, "count": count}}, ensure_ascii=True))

        print(json.dumps(
            {
                "threads_sqlite_master": rows(
                    con,
                    "select type,name,sql from sqlite_master "
                    "where tbl_name='threads' and sql is not null "
                    "order by type,name",
                )
            },
            ensure_ascii=True,
            indent=2,
        ))
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
