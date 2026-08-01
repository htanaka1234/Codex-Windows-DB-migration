import json
import os
import shutil
import sys
from datetime import datetime
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


def windows_cwd(value: str, wsl_distro: str | None = None) -> str:
    return to_windows_path(value, long_prefix=False, wsl_distro=wsl_distro)


def wsl_cwd(value: str) -> str:
    return to_wsl_path(value)


def normalize_cwd(value: str, target_style: str, wsl_distro: str | None = None) -> str:
    if target_style == "wsl":
        return wsl_cwd(value)
    if target_style == "windows":
        return windows_cwd(value, wsl_distro)
    raise ValueError(f"unsupported target style: {target_style}")


def desired_thread_source(source):
    if source == "vscode":
        return "user"
    if isinstance(source, dict) and "subagent" in source:
        return "subagent"
    return None


def normalize_structured_paths(value, target_style: str, wsl_distro: str | None, location: str, changes):
    """Normalize typed path fields without rewriting paths embedded in free text."""
    if isinstance(value, dict):
        for key, child in value.items():
            child_location = f"{location}.{key}" if location else key
            if key == "cwd" and isinstance(child, str):
                new_value = normalize_cwd(child, target_style, wsl_distro)
                if new_value != child:
                    value[key] = new_value
                    changes.append({"field": child_location, "old": child, "new": new_value})
            elif key in {"writable_roots", "writableRoots"} and isinstance(child, list):
                for index, item in enumerate(child):
                    if not isinstance(item, str):
                        continue
                    new_value = normalize_cwd(item, target_style, wsl_distro)
                    if new_value != item:
                        child[index] = new_value
                        changes.append({
                            "field": f"{child_location}[{index}]",
                            "old": item,
                            "new": new_value,
                        })
            else:
                normalize_structured_paths(child, target_style, wsl_distro, child_location, changes)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            normalize_structured_paths(child, target_style, wsl_distro, f"{location}[{index}]", changes)


def analyze_file(path: Path, target_style: str, wsl_distro: str | None = None):
    raw_lines = path.read_bytes().splitlines(keepends=True)
    output = []
    changes = []
    errors = []
    thread_id = None
    source = None

    for line_number, raw_line in enumerate(raw_lines, 1):
        content = raw_line.rstrip(b"\r\n")
        line_ending = raw_line[len(content) :]
        if not content:
            output.append(raw_line)
            continue
        try:
            obj = json.loads(content.decode("utf-8"))
        except Exception as exc:
            errors.append({
                "path": str(path),
                "line": line_number,
                "error": str(exc),
                "blocking": True,
            })
            output.append(raw_line)
            continue

        line_changes = []
        try:
            normalize_structured_paths(obj, target_style, wsl_distro, "", line_changes)
        except PathConversionError as exc:
            errors.append({
                "path": str(path),
                "line": line_number,
                "error": str(exc),
                "blocking": True,
            })
            output.append(raw_line)
            continue

        payload = obj.get("payload")
        if obj.get("type") == "session_meta" and isinstance(payload, dict):
            thread_id = thread_id or payload.get("id")
            source = source or payload.get("source")
            wanted_source = desired_thread_source(payload.get("source"))
            current_source = payload.get("thread_source")
            if wanted_source and current_source != wanted_source:
                payload["thread_source"] = wanted_source
                line_changes.append({
                    "field": "payload.thread_source",
                    "old": current_source,
                    "new": wanted_source,
                })

        if line_changes:
            for change in line_changes:
                change["line"] = line_number
            changes.extend(line_changes)
            output.append(
                json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                + (line_ending or b"\n")
            )
        else:
            output.append(raw_line)

    if not changes:
        return None, errors
    return {
        "path": path,
        "content": b"".join(output),
        "changes": changes,
        "id": thread_id,
        "source": source,
    }, errors


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
                "usage: repair_rollout_session_meta.py [dry-run|apply] "
                "[--target-style windows|wsl|auto] [--wsl-distro NAME] "
                "[--codex-home PATH] [--backup-root PATH]"
            )
        i += 1

    if mode not in {"dry-run", "apply"}:
        raise SystemExit("usage: repair_rollout_session_meta.py [dry-run|apply]")
    if target_style == "auto":
        target_style = default_target_style()
    if target_style not in {"windows", "wsl"}:
        raise SystemExit("--target-style must be windows, wsl, or auto")

    candidates = []
    errors = []
    session_files = []
    for directory in (codex_home / "sessions", codex_home / "archived_sessions"):
        if directory.exists():
            session_files.extend(directory.glob("**/rollout-*.jsonl"))
    session_files = sorted(session_files)
    for path in session_files:
        candidate, file_errors = analyze_file(path, target_style, wsl_distro)
        errors.extend(file_errors)
        if candidate:
            candidates.append(candidate)

    summary = {
        "mode": mode,
        "target_style": target_style,
        "codex_home": str(codex_home),
        "wsl_distro": wsl_distro,
        "session_files_scanned": len(session_files),
        "files_to_update": len(candidates),
        "structured_changes": sum(len(item["changes"]) for item in candidates),
        "errors": errors[:20],
        "sample": [
            {
                "path": str(item["path"]),
                "id": item["id"],
                "source": item["source"],
                "change_count": len(item["changes"]),
                "changes": item["changes"][:20],
            }
            for item in candidates[:20]
        ],
    }
    print(json.dumps({"plan": summary}, ensure_ascii=False, indent=2, default=str))

    if any(error.get("blocking") for error in errors):
        print("Refusing to continue because one or more cwd values cannot be converted safely.", file=sys.stderr)
        return 2

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

        src.write_bytes(item["content"])

    print(json.dumps({
        "applied": {
            "backup_dir": str(backup_dir),
            "files_updated": len(candidates),
        }
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
