"""Normalize typed filesystem paths stored in Codex global UI state."""

import copy
import re

from codex_path_styles import PathConversionError, normalize_path


TOP_LEVEL_PATH_LISTS = {
    "active-workspace-roots",
    "electron-saved-workspace-roots",
    "project-order",
}
NESTED_PATH_LIST_KEYS = {"rootPaths", "writableRoots", "writable_roots"}


def looks_like_path(value: str) -> bool:
    return bool(
        value.startswith(("/", "\\", "UNC\\"))
        or re.match(r"^[A-Za-z]:[\\/]", value)
    )


def normalize_global_state(state, target_style: str, wsl_distro: str | None = None):
    """Return a normalized copy plus changes and blocking conversion errors."""
    result = copy.deepcopy(state)
    changes = []
    errors = []

    def convert(value: str, location: str) -> str:
        try:
            new_value = normalize_path(value, target_style, wsl_distro=wsl_distro)
        except PathConversionError as exc:
            errors.append({"source": location, "value": value, "error": str(exc)})
            return value
        if new_value != value:
            changes.append({"source": location, "old": value, "new": new_value})
        return new_value

    def visit(value, location: str):
        if isinstance(value, dict):
            rewritten = {}
            for key, child in value.items():
                key_location = f"{location}.<key>" if location else "<key>"
                new_key = convert(key, key_location) if isinstance(key, str) and looks_like_path(key) else key
                child_location = f"{location}.{new_key}" if location else str(new_key)
                if new_key in rewritten:
                    errors.append({
                        "source": key_location,
                        "value": key,
                        "error": f"path-key conversion collides with existing key {new_key!r}",
                    })
                    new_key = key
                if new_key == "cwd" and isinstance(child, str):
                    rewritten[new_key] = convert(child, child_location)
                elif new_key in NESTED_PATH_LIST_KEYS and isinstance(child, list):
                    rewritten[new_key] = [
                        convert(item, f"{child_location}[{index}]") if isinstance(item, str) and looks_like_path(item) else visit(item, f"{child_location}[{index}]")
                        for index, item in enumerate(child)
                    ]
                else:
                    rewritten[new_key] = visit(child, child_location)
            value.clear()
            value.update(rewritten)
            return value
        if isinstance(value, list):
            for index, child in enumerate(value):
                value[index] = visit(child, f"{location}[{index}]")
        return value

    visit(result, "")

    for key in TOP_LEVEL_PATH_LISTS:
        values = result.get(key)
        if not isinstance(values, list):
            continue
        seen = set()
        normalized = []
        for index, item in enumerate(values):
            new_item = convert(item, f"{key}[{index}]") if isinstance(item, str) and looks_like_path(item) else item
            marker = new_item.lower() if isinstance(new_item, str) else repr(new_item)
            if marker not in seen:
                seen.add(marker)
                normalized.append(new_item)
        result[key] = normalized

    hints = result.get("thread-workspace-root-hints")
    if isinstance(hints, dict):
        for thread_id, value in hints.items():
            if isinstance(value, str) and looks_like_path(value):
                hints[thread_id] = convert(value, f"thread-workspace-root-hints.{thread_id}")

    writable_roots = result.get("thread-writable-roots")
    if isinstance(writable_roots, dict):
        for thread_id, values in writable_roots.items():
            if not isinstance(values, list):
                continue
            writable_roots[thread_id] = [
                convert(value, f"thread-writable-roots.{thread_id}[{index}]")
                if isinstance(value, str) and looks_like_path(value)
                else value
                for index, value in enumerate(values)
            ]

    return result, changes, errors


def iter_global_state_paths(state):
    """Yield typed path locations and values from global UI state."""
    found = []
    seen = set()

    def emit(location: str, value: str):
        marker = (location, value)
        if marker not in seen:
            seen.add(marker)
            found.append(marker)

    def visit(value, location: str):
        if isinstance(value, dict):
            for key, child in value.items():
                if isinstance(key, str) and looks_like_path(key):
                    emit(f"{location}.<key>" if location else "<key>", key)
                child_location = f"{location}.{key}" if location else str(key)
                if key == "cwd" and isinstance(child, str) and looks_like_path(child):
                    emit(child_location, child)
                elif key in NESTED_PATH_LIST_KEYS and isinstance(child, list):
                    for index, item in enumerate(child):
                        if isinstance(item, str) and looks_like_path(item):
                            emit(f"{child_location}[{index}]", item)
                        else:
                            visit(item, f"{child_location}[{index}]")
                else:
                    visit(child, child_location)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, f"{location}[{index}]")

    visit(state, "")
    for key in TOP_LEVEL_PATH_LISTS:
        for index, value in enumerate(state.get(key, [])):
            if isinstance(value, str) and looks_like_path(value):
                emit(f"{key}[{index}]", value)
    for key in ("thread-workspace-root-hints", "thread-writable-roots"):
        mapping = state.get(key, {})
        if not isinstance(mapping, dict):
            continue
        for thread_id, value in mapping.items():
            values = value if isinstance(value, list) else [value]
            for index, item in enumerate(values):
                if isinstance(item, str) and looks_like_path(item):
                    suffix = f"[{index}]" if isinstance(value, list) else ""
                    emit(f"{key}.{thread_id}{suffix}", item)
    return found
