import json
import os
import re
import shutil
import sys
from datetime import datetime
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


def default_backup_root() -> Path:
    if os.environ.get("RECOVERY_WORKDIR"):
        return Path(os.environ["RECOVERY_WORKDIR"])
    if os.environ.get("CODEX_RECOVERY_WORKDIR"):
        return Path(os.environ["CODEX_RECOVERY_WORKDIR"])
    cwd = Path.cwd()
    if cwd.exists():
        return cwd
    return default_codex_home()


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


def windows_cwd(value: str) -> str:
    value = strip_long_windows_prefix(value or "")
    m = MNT_RE.match(value or "")
    if not m:
        m = DRIVE_RE.match(value or "")
        if m:
            drive = m.group(1).upper()
            rest = (m.group(2) or "").replace("/", "\\").rstrip("\\")
            return f"{drive}:\\{rest}" if rest else f"{drive}:\\"
        return value.replace("/", "\\").rstrip("\\")
    drive = m.group(1).upper()
    rest = (m.group(2) or "").replace("/", "\\")
    if rest:
        return f"{drive}:\\{rest}"
    return f"{drive}:\\"


def wsl_cwd(value: str) -> str:
    value = strip_long_windows_prefix(value or "")
    m = MNT_RE.match(value.replace("\\", "/"))
    if m:
        drive = m.group(1).lower()
        rest = (m.group(2) or "").replace("\\", "/").strip("/")
        return f"/mnt/{drive}/{rest}" if rest else f"/mnt/{drive}"

    m = DRIVE_RE.match(value)
    if not m:
        return value.replace("\\", "/").rstrip("/")

    drive = m.group(1).lower()
    rest = (m.group(2) or "").replace("\\", "/").strip("/")
    return f"/mnt/{drive}/{rest}" if rest else f"/mnt/{drive}"


def normalize_cwd(value: str, target_style: str) -> str:
    if target_style == "wsl":
        return wsl_cwd(value)
    if target_style == "windows":
        return windows_cwd(value)
    raise ValueError(f"unsupported target style: {target_style}")


def desired_thread_source(source):
    if source == "vscode":
        return "user"
    if isinstance(source, dict) and "subagent" in source:
        return "subagent"
    return None


def analyze_file(path: Path, target_style: str):
    raw = path.read_bytes()
    newline = raw.find(b"\n")
    if newline < 0:
        first = raw
        rest = b""
    else:
        first = raw[:newline]
        rest = raw[newline + 1 :]
    try:
        obj = json.loads(first.decode("utf-8"))
    except Exception as exc:
        return None, {"path": str(path), "error": str(exc)}
    if obj.get("type") != "session_meta" or not isinstance(obj.get("payload"), dict):
        return None, None

    payload = obj["payload"]
    changes = {}

    cwd = payload.get("cwd")
    new_cwd = normalize_cwd(cwd, target_style) if isinstance(cwd, str) else cwd
    if new_cwd != cwd:
        changes["cwd"] = {"old": cwd, "new": new_cwd}

    wanted_source = desired_thread_source(payload.get("source"))
    current_source = payload.get("thread_source")
    if wanted_source and current_source != wanted_source:
        changes["thread_source"] = {"old": current_source, "new": wanted_source}

    if not changes:
        return None, None

    return {
        "path": path,
        "obj": obj,
        "rest": rest,
        "changes": changes,
        "id": payload.get("id"),
        "source": payload.get("source"),
    }, None


def write_file(path: Path, obj, rest: bytes):
    first = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
    path.write_bytes(first + rest)


def main() -> int:
    args = sys.argv[1:]
    mode = "dry-run"
    target_style = default_target_style()
    codex_home = default_codex_home()
    backup_root = default_backup_root()

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
        else:
            raise SystemExit(
                "usage: repair_rollout_session_meta.py [dry-run|apply] "
                "[--target-style windows|wsl|auto] [--codex-home PATH] [--backup-root PATH]"
            )
        i += 1

    if mode not in {"dry-run", "apply"}:
        raise SystemExit("usage: repair_rollout_session_meta.py [dry-run|apply]")
    if target_style == "auto":
        target_style = default_target_style()
    if target_style not in {"windows", "wsl"}:
        raise SystemExit("--target-style must be windows, wsl, or auto")

    sessions = codex_home / "sessions"

    candidates = []
    errors = []
    session_files = sorted(sessions.glob("**/rollout-*.jsonl"))
    for path in session_files:
        candidate, error = analyze_file(path, target_style)
        if error:
            errors.append(error)
        if candidate:
            candidates.append(candidate)

    summary = {
        "mode": mode,
        "target_style": target_style,
        "codex_home": str(codex_home),
        "session_files_scanned": len(session_files),
        "files_to_update": len(candidates),
        "errors": errors[:20],
        "sample": [
            {
                "path": str(item["path"]),
                "id": item["id"],
                "source": item["source"],
                "changes": item["changes"],
            }
            for item in candidates[:20]
        ],
    }
    print(json.dumps({"plan": summary}, ensure_ascii=False, indent=2, default=str))

    if mode == "dry-run":
        return 0

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = backup_root / f"rollout-session-meta-backup-{target_style}-{ts}"
    for item in candidates:
        src = item["path"]
        rel = src.relative_to(codex_home)
        dst = backup_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

        payload = item["obj"]["payload"]
        for key, change in item["changes"].items():
            payload[key] = change["new"]
        write_file(src, item["obj"], item["rest"])

    print(json.dumps({
        "applied": {
            "backup_dir": str(backup_dir),
            "files_updated": len(candidates),
        }
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
