from pathlib import Path
import sqlite3
import sys
import hashlib


BASE = Path(r"C:\Program Files\WindowsApps")
ROOT = Path(__file__).resolve().parents[1]
STATE_DB = ROOT / "state_5.sqlite"
PATTERNS = [
    b"threads preview",
    b"thread goals",
    b"_sqlx_migrations",
    b"checksum mismatch",
    b"migrations",
    b"CREATE TABLE threads",
    b"thread_dynamic_tools",
]


def printable(data: bytes) -> str:
    return "".join(chr(b) if 32 <= b < 127 else "." for b in data)


def scan_file(path: Path) -> None:
    try:
        data = path.read_bytes()
    except Exception as exc:
        print("READ_ERROR", path, repr(exc))
        return
    for pattern in PATTERNS:
        start = 0
        found = 0
        while True:
            pos = data.find(pattern, start)
            if pos < 0:
                break
            found += 1
            lo = max(0, pos - 220)
            hi = min(len(data), pos + len(pattern) + 420)
            print("MATCH", path, pattern.decode("utf-8", "replace"), pos)
            print(printable(data[lo:hi]))
            start = pos + len(pattern)
            if found >= 8:
                print("MATCH_LIMIT", path, pattern.decode("utf-8", "replace"))
                break


def load_db_migrations():
    con = sqlite3.connect(f"file:{STATE_DB.as_posix()}?mode=ro", uri=True)
    try:
        return con.execute(
            "select version,description,checksum from _sqlx_migrations order by version"
        ).fetchall()
    finally:
        con.close()


def compare_checksums(path: Path) -> None:
    data = path.read_bytes()
    rows = load_db_migrations()
    print("compare_file:", path)
    for version, description, checksum in rows:
        checksum_pos = data.find(checksum)
        desc = description.encode()
        candidates = []
        start = 0
        while True:
            pos = data.find(desc, start)
            if pos < 0:
                break
            tail = data[pos + len(desc):pos + len(desc) + 24]
            if tail.startswith((b"CREATE", b"ALTER", b"DROP", b"UPDATE", b"INSERT")):
                candidates.append(pos)
            start = pos + 1
        print(
            f"v{version:02d}",
            description,
            "db_checksum_found=" + str(checksum_pos),
            "migration_desc_candidates=" + ",".join(str(c) for c in candidates[:8]),
        )


def boundary_probe(path: Path, first: str, second: str) -> None:
    data = path.read_bytes()
    a = data.find(first.encode())
    b = data.find(second.encode())
    print("boundary", first, a, second, b)
    if a < 0 or b < 0:
        return
    lo = max(a, b - 180)
    hi = min(len(data), b + len(second) + 180)
    chunk = data[lo:hi]
    for i in range(0, len(chunk), 16):
        part = chunk[i:i + 16]
        print(f"{lo + i:012d}", part.hex(" "), printable(part))


def extract_expected(path: Path) -> list[tuple[int, str, bytes, int, int]]:
    rows = load_db_migrations()
    matches = scan_migration_sql_hashes(path)
    if len(matches) < len(rows):
        raise RuntimeError(f"found only {len(matches)} migration SQL/checksum pairs")
    matches = matches[: len(rows)]
    return [
        (rows[i][0], rows[i][1], matches[i][2], matches[i][0], matches[i][1])
        for i in range(len(rows))
    ]


def scan_migration_sql_hashes(path: Path) -> list[tuple[int, int, bytes]]:
    data = path.read_bytes()
    first_pos = data.find(b"threadsCREATE TABLE threads")
    if first_pos < 0:
        raise RuntimeError("failed to locate first migration")
    block = data[first_pos:first_pos + 40000]
    keywords = (b"CREATE", b"ALTER", b"DROP", b"UPDATE", b"INSERT", b"PRAGMA")
    starts = []
    for keyword in keywords:
        start = 0
        while True:
            pos = block.find(keyword, start)
            if pos < 0:
                break
            starts.append(first_pos + pos)
            start = pos + 1

    matches: list[tuple[int, int, bytes]] = []
    for sql_start in sorted(set(starts)):
        max_end = min(len(data) - 48, sql_start + 12000)
        for sql_end in range(sql_start + 3, max_end):
            if data[sql_end - 1] != 0x0A:
                continue
            if data[sql_end - 2] != 0x3B and not (
                sql_end >= 3 and data[sql_end - 3] == 0x3B and data[sql_end - 2] == 0x0D
            ):
                continue
            sql = data[sql_start:sql_end]
            checksum = data[sql_end:sql_end + 48]
            if hashlib.sha384(sql).digest() == checksum:
                matches.append((sql_start, sql_end, checksum))
                break

    deduped = []
    last_end = -1
    for sql_start, sql_end, checksum in sorted(matches):
        if sql_start < last_end:
            continue
        deduped.append((sql_start, sql_end, checksum))
        last_end = sql_end + 48
    return deduped


def print_expected(path: Path) -> None:
    rows = load_db_migrations()
    db_by_version = {version: checksum for version, _, checksum in rows}
    print("expected_file:", path)
    extracted = extract_expected(path)
    for version, description, checksum, pos, sql_end in extracted:
        old = db_by_version[version]
        print(
            json_line(
                {
                    "version": version,
                    "description": description,
                    "pos": pos,
                    "sql_end": sql_end,
                    "checksum": checksum.hex(),
                    "matches_db": checksum == old,
                }
            )
        )


def print_matches(path: Path) -> None:
    data = path.read_bytes()
    matches = scan_migration_sql_hashes(path)
    print("match_count:", len(matches))
    for i, (start, end, checksum) in enumerate(matches, 1):
        prefix = printable(data[start:start + 80]).replace("\n", ".")
        print(json_line({"index": i, "start": start, "end": end, "checksum": checksum.hex(), "prefix": prefix}))


def json_line(obj) -> str:
    import json

    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def main() -> int:
    print("base_exists:", BASE.exists())
    try:
        packages = sorted(BASE.glob("OpenAI.Codex_*_x64__2p2nqsd0c76g0"))
    except Exception as exc:
        print("glob_error:", repr(exc))
        packages = []
    known = BASE / "OpenAI.Codex_26.519.2081.0_x64__2p2nqsd0c76g0"
    if known not in packages:
        packages.append(known)
    print("packages:", [str(p) for p in packages])
    for package in packages[-2:]:
        resources = package / "app" / "resources"
        print("resources:", resources)
        if len(sys.argv) > 1 and sys.argv[1] == "dump":
            path = resources / "codex"
            data = path.read_bytes()
            start = int(sys.argv[2])
            length = int(sys.argv[3])
            for i in range(start, min(len(data), start + length), 16):
                part = data[i:i + 16]
                print(f"{i:012d}", part.hex(" "), printable(part))
            continue
        for name in ["codex.exe", "codex", "app.asar", "node_repl.exe"]:
            path = resources / name
            if path.exists():
                print("file:", path, "size:", path.stat().st_size)
                if len(sys.argv) == 1 or sys.argv[1] == "scan":
                    scan_file(path)
                if name in {"codex.exe", "codex"}:
                    if len(sys.argv) == 1 or sys.argv[1] in {"compare", "scan"}:
                        compare_checksums(path)
                        boundary_probe(path, "drop device key bindings", "threads preview")
                    if len(sys.argv) > 1 and sys.argv[1] == "expected" and name == "codex":
                        print_expected(path)
                    if len(sys.argv) > 1 and sys.argv[1] == "matches" and name == "codex":
                        print_matches(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
