"""Codex state path conversion with explicit Windows absolute-path checks."""

import re


MNT_RE = re.compile(r"^/mnt/([A-Za-z])(?:/(.*))?$")
DRIVE_RE = re.compile(r"^([A-Za-z]):(?:[\\/](.*))?$")
WINDOWS_DRIVE_ABSOLUTE_RE = re.compile(r"^[A-Za-z]:[\\/]")
MALFORMED_UNC_RE = re.compile(r"^UNC[\\/]", re.IGNORECASE)
WSL_UNC_RE = re.compile(
    r"^\\\\(?:wsl\.localhost|wsl\$)\\([^\\]+)(?:\\(.*))?$",
    re.IGNORECASE,
)


class PathConversionError(ValueError):
    """Raised when a path cannot be represented safely for the target OS."""


def strip_long_windows_prefix(value: str) -> str:
    """Remove a Win32 device prefix without corrupting an extended UNC path."""
    long_unc = "\\\\?\\UNC\\"
    device_unc = "\\\\.\\UNC\\"
    if value.upper().startswith(long_unc.upper()):
        return "\\\\" + value[len(long_unc) :]
    if value.upper().startswith(device_unc.upper()):
        return "\\\\" + value[len(device_unc) :]
    if value.startswith("\\\\?\\") or value.startswith("\\\\.\\"):
        return value[4:]
    return value


def _standard_unc(value: str) -> str:
    normalized = value.replace("/", "\\")
    if MALFORMED_UNC_RE.match(normalized):
        return "\\\\" + normalized[4:]
    return normalized


def is_windows_absolute_path(value: str) -> bool:
    if not value:
        return False
    normalized = _standard_unc(strip_long_windows_prefix(value))
    if WINDOWS_DRIVE_ABSOLUTE_RE.match(normalized):
        return True
    if not normalized.startswith("\\\\"):
        return False
    parts = [part for part in normalized[2:].split("\\") if part]
    return len(parts) >= 2


def windows_path_problem(value: str) -> str | None:
    """Return an actionable reason when a value is unsafe as a Windows cwd."""
    if not value:
        return "empty path"
    if MALFORMED_UNC_RE.match(value):
        return "UNC prefix is missing its leading double backslash"
    normalized = strip_long_windows_prefix(value).replace("/", "\\")
    if re.match(r"^[A-Za-z]:\\mnt\\[A-Za-z](?:\\|$)", normalized, re.IGNORECASE):
        return "path looks like a WSL /mnt path incorrectly nested below a Windows drive"
    if not is_windows_absolute_path(value):
        return "path is not an absolute Windows drive or UNC path"
    return None


def to_windows_path(value: str, *, long_prefix: bool, wsl_distro: str | None = None) -> str:
    """Convert a Codex cwd to an absolute Windows drive or UNC path."""
    if not value:
        raise PathConversionError("empty cwd cannot be converted to an absolute Windows path")

    stripped = strip_long_windows_prefix(value)
    posix_native = stripped.startswith("/") and not stripped.startswith("//")
    raw = _standard_unc(stripped)
    slash_value = raw.replace("\\", "/")
    m = MNT_RE.match(slash_value)
    if m:
        drive = m.group(1).upper()
        rest = (m.group(2) or "").replace("/", "\\").rstrip("\\")
        standard = f"{drive}:\\{rest}" if rest else f"{drive}:\\"
    else:
        m = DRIVE_RE.match(raw)
        if m:
            drive = m.group(1).upper()
            rest = (m.group(2) or "").replace("/", "\\").rstrip("\\")
            standard = f"{drive}:\\{rest}" if rest else f"{drive}:\\"
        elif raw.startswith("\\\\"):
            standard = raw.replace("/", "\\").rstrip("\\")
        elif posix_native:
            if not wsl_distro:
                raise PathConversionError(
                    f"WSL-native cwd {value!r} requires --wsl-distro (for example, Ubuntu)"
                )
            distro = wsl_distro.strip("\\/")
            if not distro:
                raise PathConversionError("--wsl-distro must not be empty")
            rest = stripped.lstrip("/").replace("/", "\\")
            standard = f"\\\\wsl.localhost\\{distro}\\{rest}" if rest else f"\\\\wsl.localhost\\{distro}"
        else:
            raise PathConversionError(
                f"cwd {value!r} is not an absolute Windows path and cannot be converted safely"
            )

    problem = windows_path_problem(standard)
    if problem:
        raise PathConversionError(f"cwd {value!r}: {problem}")
    if not long_prefix:
        return standard
    if standard.startswith("\\\\"):
        return "\\\\?\\UNC\\" + standard[2:]
    return "\\\\?\\" + standard


def to_wsl_path(value: str) -> str:
    """Convert drive paths and WSL UNC paths to WSL-native absolute paths."""
    if not value:
        return ""
    raw = _standard_unc(strip_long_windows_prefix(value))
    wsl_unc = WSL_UNC_RE.match(raw)
    if wsl_unc:
        rest = (wsl_unc.group(2) or "").replace("\\", "/").strip("/")
        return f"/{rest}" if rest else "/"
    normalized = raw.replace("\\", "/")
    m = MNT_RE.match(normalized)
    if m:
        drive = m.group(1).lower()
        rest = (m.group(2) or "").strip("/")
        return f"/mnt/{drive}/{rest}" if rest else f"/mnt/{drive}"
    m = DRIVE_RE.match(raw)
    if m:
        drive = m.group(1).lower()
        rest = (m.group(2) or "").replace("\\", "/").strip("/")
        return f"/mnt/{drive}/{rest}" if rest else f"/mnt/{drive}"
    return normalized.rstrip("/") or "/"


def normalize_path(
    value: str,
    target_style: str,
    *,
    windows_long_prefix: bool = False,
    wsl_distro: str | None = None,
) -> str:
    if target_style == "windows":
        return to_windows_path(value, long_prefix=windows_long_prefix, wsl_distro=wsl_distro)
    if target_style == "wsl":
        return to_wsl_path(value)
    raise ValueError(f"unsupported target style: {target_style}")
