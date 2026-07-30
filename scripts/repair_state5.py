import hashlib
import importlib.util
import json
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "state_5.sqlite"
STAMP = datetime.now().strftime("%Y%m%d-%H%M%S")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def connect_readonly(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)


def inspect(path: Path = DB) -> None:
    print(json.dumps({
        "path": str(path),
        "exists": path.exists(),
        "size": path.stat().st_size if path.exists() else None,
        "sha256": sha256(path) if path.exists() else None,
    }, indent=2))
    if not path.exists():
        return

    header = path.read_bytes()[:128]
    print("header_ascii:", header[:16])
    print("header_hex:", header.hex(" "))

    con = connect_readonly(path)
    try:
        for pragma in [
            "page_size",
            "page_count",
            "journal_mode",
            "schema_version",
            "user_version",
            "application_id",
            "freelist_count",
        ]:
            print(f"{pragma}:", con.execute(f"pragma {pragma}").fetchone()[0])
        print("quick_check:")
        for row in con.execute("pragma quick_check"):
            print(row[0])
        print("integrity_check:")
        for row in con.execute("pragma integrity_check"):
            print(row[0])
        rows = con.execute(
            "select type,name,sql from sqlite_schema order by type,name"
        ).fetchall()
        print("schema_objects:", len(rows))
        for type_, name, sql in rows:
            normalized = " ".join((sql or "").split())
            print(type_, name, normalized[:300])
        print("table_counts:")
        for (name,) in con.execute(
            "select name from sqlite_schema where type='table' order by name"
        ):
            try:
                count = con.execute(f"select count(*) from {quote_ident(name)}").fetchone()[0]
                print(name, count)
            except Exception as exc:
                print(name, "ERROR", repr(exc))
    finally:
        con.close()


def migrations(path: Path = DB) -> None:
    con = connect_readonly(path)
    try:
        rows = con.execute(
            "select version,description,installed_on,success,hex(checksum),execution_time "
            "from _sqlx_migrations order by version"
        ).fetchall()
        print("migration_count:", len(rows))
        for version, description, installed_on, success, checksum, execution_time in rows:
            print(
                json.dumps(
                    {
                        "version": version,
                        "description": description,
                        "installed_on": installed_on,
                        "success": success,
                        "checksum": checksum.lower(),
                        "execution_time": execution_time,
                    },
                    ensure_ascii=False,
                )
            )
    finally:
        con.close()


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def copy_backup(path: Path) -> Path:
    backup = path.with_name(f"{path.name}.orig-{STAMP}")
    shutil.copy2(path, backup)
    print("backup:", backup)
    return backup


def backup_api_repair(path: Path = DB) -> Path:
    copy_backup(path)
    out = path.with_name(f"{path.stem}.repaired-backup-api-{STAMP}{path.suffix}")
    if out.exists():
        raise FileExistsError(out)
    src = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(out)
        try:
            src.backup(dst)
            dst.execute("pragma wal_checkpoint(truncate)")
            dst.execute("vacuum")
            dst.commit()
        finally:
            dst.close()
    finally:
        src.close()
    print("repaired:", out)
    inspect(out)
    return out


def logical_dump_repair(path: Path = DB) -> Path:
    copy_backup(path)
    out = path.with_name(f"{path.stem}.repaired-dump-{STAMP}{path.suffix}")
    if out.exists():
        raise FileExistsError(out)
    src = connect_readonly(path)
    try:
        script = "\n".join(src.iterdump())
    finally:
        src.close()
    dst = sqlite3.connect(out)
    try:
        dst.executescript(script)
        dst.execute("vacuum")
        dst.commit()
    finally:
        dst.close()
    print("repaired:", out)
    inspect(out)
    return out


def replace_with(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(path)
    copy_backup(DB)
    shutil.copy2(path, DB)
    print("installed:", DB)
    inspect(DB)


def find_linux_codex_binary() -> Path:
    candidates: list[Path] = []
    try:
        result = subprocess.run(
            ["where.exe", "codex"],
            check=False,
            capture_output=True,
            text=True,
        )
        for line in result.stdout.splitlines():
            path = Path(line.strip())
            if path.name == "codex":
                candidates.append(path)
    except Exception:
        pass

    candidates.append(
        Path(
            r"C:\Program Files\WindowsApps"
            r"\OpenAI.Codex_26.519.2081.0_x64__2p2nqsd0c76g0"
            r"\app\resources\codex"
        )
    )

    for path in candidates:
        if path.exists():
            return path
    raise FileNotFoundError("Linux codex binary was not found")


def load_scanner():
    script = Path(__file__).with_name("scan_codex_app.py")
    spec = importlib.util.spec_from_file_location("scan_codex_app", script)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load {script}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def repair_linux_checksums(path: Path = DB) -> None:
    scanner = load_scanner()
    codex_binary = find_linux_codex_binary()
    expected_rows = scanner.extract_expected(codex_binary)
    expected = {
        version: (description, checksum)
        for version, description, checksum, _pos, _sql_end in expected_rows
    }

    con = sqlite3.connect(path)
    try:
        actual_rows = con.execute(
            "select version,description,checksum from _sqlx_migrations order by version"
        ).fetchall()
        if set(expected) != {version for version, _description, _checksum in actual_rows}:
            raise RuntimeError("migration version set does not match bundled codex binary")
        mismatches = []
        for version, description, checksum in actual_rows:
            expected_description, expected_checksum = expected[version]
            if description != expected_description:
                raise RuntimeError(
                    f"migration description mismatch for v{version}: "
                    f"{description!r} != {expected_description!r}"
                )
            if checksum != expected_checksum:
                mismatches.append((version, description, checksum, expected_checksum))

        if not mismatches:
            print("no checksum changes needed")
            return

        copy_backup(path)
        for version, _description, _old_checksum, new_checksum in mismatches:
            con.execute(
                "update _sqlx_migrations set checksum = ? where version = ?",
                (new_checksum, version),
            )
        con.commit()
        con.execute("pragma wal_checkpoint(truncate)")
        con.commit()
        print("codex_binary:", codex_binary)
        print("updated_migrations:", len(mismatches))
        for version, description, old_checksum, new_checksum in mismatches:
            print(
                json.dumps(
                    {
                        "version": version,
                        "description": description,
                        "old_checksum": old_checksum.hex(),
                        "new_checksum": new_checksum.hex(),
                    },
                    ensure_ascii=False,
                )
            )
    finally:
        con.close()
    inspect(path)


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "inspect"
    path_arg = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else DB
    if cmd == "inspect":
        inspect(path_arg)
    elif cmd == "migrations":
        migrations(path_arg)
    elif cmd == "repair-backup-api":
        backup_api_repair()
    elif cmd == "repair-dump":
        logical_dump_repair()
    elif cmd == "replace":
        replace_with(Path(sys.argv[2]).resolve())
    elif cmd == "repair-linux-checksums":
        repair_linux_checksums(path_arg)
    else:
        print(
            "usage: repair_state5.py "
            "[inspect|migrations|repair-backup-api|repair-dump|replace PATH|repair-linux-checksums]"
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
