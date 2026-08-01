"""Inspect and safely rewrite agents.<role>.config_file values in config.toml."""

import json
import ntpath
import posixpath
import re
import tomllib
from pathlib import Path

from codex_path_styles import (
    PathConversionError,
    is_windows_absolute_path,
    to_windows_path,
    to_wsl_path,
    windows_path_problem,
)


CONFIG_ASSIGNMENT_RE = re.compile(
    r"^(?P<prefix>\s*config_file\s*=\s*)(?P<value>\"(?:\\.|[^\"])*\"|'[^']*')(?P<suffix>\s*(?:#.*)?(?:\r?\n)?)$"
)


def is_declared_relative(value: str) -> bool:
    malformed_unc = value.lower().startswith(("unc\\", "unc/"))
    drive_qualified = bool(re.match(r"^[A-Za-z]:", value))
    return (
        not value.startswith("/")
        and not value.startswith("\\")
        and not malformed_unc
        and not drive_qualified
        and not is_windows_absolute_path(value)
    )


def load_agent_config_entries(config_path: Path) -> list[dict]:
    data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    agents = data.get("agents", {})
    if not isinstance(agents, dict):
        return []
    entries = []
    for role, settings in agents.items():
        if isinstance(settings, dict) and isinstance(settings.get("config_file"), str):
            entries.append({"role": role, "value": settings["config_file"]})
    return entries


def resolve_relative_for_target(value: str, config_path: Path, target_style: str, wsl_distro: str | None) -> str:
    if target_style == "windows":
        base = to_windows_path(str(config_path.parent), long_prefix=False, wsl_distro=wsl_distro)
        return ntpath.normpath(ntpath.join(base, value.replace("/", "\\")))
    base = to_wsl_path(str(config_path.parent))
    return posixpath.normpath(posixpath.join(base, value.replace("\\", "/")))


def normalize_agent_config_value(
    value: str,
    config_path: Path,
    target_style: str,
    wsl_distro: str | None,
    *,
    absolutize_relative: bool = False,
) -> str:
    if is_declared_relative(value):
        if not absolutize_relative:
            return value
        return resolve_relative_for_target(value, config_path, target_style, wsl_distro)
    if target_style == "windows":
        return to_windows_path(value, long_prefix=False, wsl_distro=wsl_distro)
    return to_wsl_path(value)


def audit_agent_config(config_path: Path, target_style: str, wsl_distro: str | None = None) -> dict:
    result = {"path": str(config_path), "exists": config_path.exists(), "entries": [], "problems": []}
    if not config_path.exists():
        return result
    try:
        entries = load_agent_config_entries(config_path)
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        result["problems"].append({"source": "config.toml", "problem": f"config parse failed: {exc}"})
        return result
    for entry in entries:
        value = entry["value"]
        item = {**entry, "declared_relative": is_declared_relative(value)}
        try:
            item["resolved_for_target"] = normalize_agent_config_value(
                value,
                config_path,
                target_style,
                wsl_distro,
                absolutize_relative=True,
            )
        except PathConversionError as exc:
            item["resolved_for_target"] = None
            item["problem"] = str(exc)
        if target_style == "windows" and not item["declared_relative"]:
            item["problem"] = item.get("problem") or windows_path_problem(value)
        if item.get("problem"):
            result["problems"].append({
                "source": f"agents.{entry['role']}.config_file",
                "value": value,
                "problem": item["problem"],
            })
        result["entries"].append(item)
    return result


def rewrite_agent_config(text: str, old_values: list[str], new_values: list[str]) -> str:
    """Rewrite ordinary one-line config_file assignments while preserving other text."""
    lines = text.splitlines(keepends=True)
    indexes = []
    decoded = []
    for index, line in enumerate(lines):
        match = CONFIG_ASSIGNMENT_RE.match(line)
        if not match:
            continue
        indexes.append((index, match))
        decoded.append(tomllib.loads("config_file = " + match.group("value"))["config_file"])
    if decoded != old_values:
        raise ValueError(
            "config_file assignments could not be mapped safely; use one quoted, single-line assignment per agent role"
        )
    for (index, match), new_value in zip(indexes, new_values):
        encoded = json.dumps(new_value, ensure_ascii=False)
        lines[index] = match.group("prefix") + encoded + match.group("suffix")
    return "".join(lines)
