import json
import os
import sqlite3
from pathlib import Path


DB = Path(os.environ.get("CODEX_LOGS_DB", Path.home() / ".codex" / "logs_2.sqlite"))
TERMS = [
    "thread/list",
    "list_threads",
    "ThreadList",
    "state db",
    "state_db",
    "state_5",
    "sqlite",
    "session_index",
    "sourceKinds",
    "useStateDbOnly",
    "cwd",
    "archived",
    "preview",
    "has_user_event",
    "thread_source",
]


def rows(con, sql, args=()):
    return [dict(row) for row in con.execute(sql, args)]


def print_json(name, value):
    print(json.dumps({name: value}, ensure_ascii=False, indent=2, default=str))


def main() -> int:
    con = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        print_json("recent_error_warn", rows(
            con,
            "select id,ts,level,target,substr(feedback_log_body,1,300) body,module_path,file,line,thread_id "
            "from logs where level in ('ERROR','WARN','error','warn') "
            "order by id desc limit 80",
        ))
        print_json("recent_targets", rows(
            con,
            "select target, level, count(*) count, max(id) last_id "
            "from logs group by target, level order by last_id desc limit 80",
        ))
        for term in TERMS:
            like = f"%{term}%"
            found = rows(
                con,
                "select id,ts,level,target,substr(feedback_log_body,1,500) body,module_path,file,line,thread_id "
                "from logs "
                "where target like ? or feedback_log_body like ? or module_path like ? or file like ? "
                "order by id desc limit 20",
                (like, like, like, like),
            )
            if found:
                print_json(f"matches_{term}", found)
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
