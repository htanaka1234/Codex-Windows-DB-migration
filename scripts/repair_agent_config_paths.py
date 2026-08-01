import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

from codex_agent_config import (
    audit_agent_config,
    load_agent_config_entries,
    normalize_agent_config_value,
    rewrite_agent_config,
)
from codex_path_styles import PathConversionError


def default_codex_home() -> Path:
    if os.environ.get("CODEX_HOME"):
        return Path(os.environ["CODEX_HOME"])
    if os.environ.get("USERPROFILE"):
        return Path(os.environ["USERPROFILE"]) / ".codex"
    return Path.home() / ".codex"


def default_backup_root() -> Path:
    return Path(os.environ.get("RECOVERY_WORKDIR") or os.environ.get("CODEX_RECOVERY_WORKDIR") or Path.cwd())


def main() -> int:
    args = sys.argv[1:]
    mode = "dry-run"
    target_style = "windows"
    codex_home = default_codex_home()
    config_path = None
    backup_root = default_backup_root()
    wsl_distro = os.environ.get("WSL_DISTRO_NAME")
    absolutize_relative = False

    i = 0
    while i < len(args):
        arg = args[i]
        if arg in {"dry-run", "apply"}:
            mode = arg
        elif arg in {"--target-style", "--codex-home", "--config", "--backup-root", "--wsl-distro"}:
            i += 1
            if i >= len(args):
                raise SystemExit(f"{arg} requires a value")
            value = args[i]
            if arg == "--target-style":
                target_style = value
            elif arg == "--codex-home":
                codex_home = Path(value)
            elif arg == "--config":
                config_path = Path(value)
            elif arg == "--backup-root":
                backup_root = Path(value)
            else:
                wsl_distro = value
        elif arg == "--absolutize-relative":
            absolutize_relative = True
        else:
            raise SystemExit(
                "usage: repair_agent_config_paths.py [dry-run|apply] --target-style windows|wsl "
                "[--wsl-distro NAME] [--absolutize-relative] [--codex-home PATH] "
                "[--config PATH] [--backup-root PATH]"
            )
        i += 1

    if target_style not in {"windows", "wsl"}:
        raise SystemExit("--target-style must be windows or wsl")
    config_path = config_path or codex_home / "config.toml"
    audit = audit_agent_config(config_path, target_style, wsl_distro)
    if not audit["exists"]:
        print(json.dumps({"plan": {**audit, "mode": mode, "changes": []}}, ensure_ascii=False, indent=2))
        return 0
    if any(problem["problem"].startswith("config parse failed:") for problem in audit["problems"]):
        print(json.dumps({"plan": {**audit, "mode": mode, "changes": []}}, ensure_ascii=False, indent=2))
        return 2

    entries = load_agent_config_entries(config_path)
    new_values = []
    changes = []
    errors = []
    for entry in entries:
        try:
            new_value = normalize_agent_config_value(
                entry["value"],
                config_path,
                target_style,
                wsl_distro,
                absolutize_relative=absolutize_relative,
            )
        except PathConversionError as exc:
            errors.append({"role": entry["role"], "value": entry["value"], "error": str(exc)})
            new_value = entry["value"]
        new_values.append(new_value)
        if new_value != entry["value"]:
            changes.append({"role": entry["role"], "old": entry["value"], "new": new_value})

    plan = {
        "mode": mode,
        "target_style": target_style,
        "wsl_distro": wsl_distro,
        "config": str(config_path),
        "absolutize_relative": absolutize_relative,
        "changes": changes,
        "errors": errors,
        "audit": audit,
    }
    print(json.dumps({"plan": plan}, ensure_ascii=False, indent=2))
    if errors:
        print("Refusing to continue because one or more config_file values cannot be converted safely.", file=sys.stderr)
        return 2
    if mode == "dry-run" or not changes:
        return 0

    text = config_path.read_text(encoding="utf-8")
    try:
        rewritten = rewrite_agent_config(text, [entry["value"] for entry in entries], new_values)
    except (ValueError, KeyError) as exc:
        print(f"Refusing to rewrite config.toml: {exc}", file=sys.stderr)
        return 2

    backup_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = backup_root / f"config.before-agent-path-repair-{target_style}-{stamp}.toml"
    shutil.copy2(config_path, backup)
    temp = config_path.with_suffix(".toml.tmp-agent-path-repair")
    temp.write_text(rewritten, encoding="utf-8")
    temp.replace(config_path)
    print(json.dumps({"applied": {"config": str(config_path), "backup": str(backup), "changes": len(changes)}}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
