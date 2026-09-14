"""Machine-local markers and the MACKUP_* env contract for mackup-ng.

Layout across the XDG base directories::

    $XDG_CONFIG_HOME/mackup/
        applications/   config .toml files: sync lists + action blocks
        ignores/        ignore definitions
        markers/        LOCAL marker definitions (*.toml, same format as apps)
    $XDG_DATA_HOME/mackup/
        dconf-backup/   dconf dumps (*.dconf)
    $XDG_STATE_HOME/mackup/
        markers/        marker STATE flags
        sync-log.json   per-machine record of the last sync (see synclog.py)

Marker STATE (empty flag files toggling behaviour on this machine only, never
synced) is the only part that is machine-local. ``backup`` marks the source
machine.

Action blocks (`[run]`) receive a ``MACKUP_*`` environment contract via
:func:`hook_env`.
"""

import os
import platform
import tomllib

from . import dirs, utils
from .config import Config
from .constants import (
    MARKERS_DEFS_DIRNAME,
    PLATFORM_DARWIN,
    PLATFORM_LINUX,
    PLATFORM_WINDOWS,
)


# ---------------------------------------------------------------- paths
def backup_dir() -> str:
    """Absolute path to the storage folder Mackup syncs into (``Config.fullpath``).

    Resolves the configured storage engine/path from the config file so hooks
    and sets never hard-code ``~/Sync/Configs/Mackup``; it differs per machine.
    Falls back to an empty string if the config can't be read.
    """
    try:
        return Config().fullpath
    except (Exception, SystemExit):
        # Config() calls utils.error() (-> SystemExit) when the storage engine
        # can't be located; the env var must degrade to "" rather than abort.
        return ""


def _pkg_markers_dir() -> str:
    """Built-in marker definitions shipped inside the package."""
    here = os.path.dirname(os.path.realpath(__file__))
    return os.path.join(here, MARKERS_DEFS_DIRNAME)


def custom_markers_dir() -> str:
    """Local marker definitions: $XDG_CONFIG_HOME/mackup/markers/."""
    return dirs.custom_markers_dir()


def markers_dir() -> str:
    """Marker STATE flags: $XDG_STATE_HOME/mackup/markers/."""
    return dirs.markers_state_dir()


# ---------------------------------------------------------------- markers
def _read_marker_def(path: str) -> dict | None:
    """Parse one ``<name>.toml`` marker definition (same TOML format as apps)."""
    try:
        with open(path, "rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return None
    marker = data.get("marker")
    return marker if isinstance(marker, dict) else None


def load_marker_defs() -> dict[str, dict]:
    """Known marker definitions: package built-ins + $XDG_CONFIG_HOME/mackup/markers/.

    Each definition is a ``<name>.toml`` file (id = filename stem) in the same
    TOML format as app definitions, with a ``[marker]`` table (``name`` = human
    label, optional ``order``). Local files override built-ins of the same id.
    """
    defs: dict[str, dict] = {}
    for directory in (_pkg_markers_dir(), custom_markers_dir()):
        if not os.path.isdir(directory):
            continue
        for filename in sorted(os.listdir(directory)):
            if not filename.endswith(".toml"):
                continue
            data = _read_marker_def(os.path.join(directory, filename))
            if data is not None:
                defs[filename[: -len(".toml")]] = data
    return defs


def has_marker(name: str) -> bool:
    return os.path.isfile(os.path.join(markers_dir(), name))


def machine_role() -> str:
    """'backup' if the backup marker is present, else 'restore'."""
    return "backup" if has_marker("backup") else "restore"


def valid_marker_name(name: str) -> bool:
    """Reject empty, path-traversal and non [A-Za-z0-9._-] names."""
    if name in ("", ".", ".."):
        return False
    return all(ch.isalnum() or ch in "._-" for ch in name)


def set_marker(name: str) -> None:
    os.makedirs(markers_dir(), exist_ok=True)
    open(os.path.join(markers_dir(), name), "a").close()


def unset_marker(name: str) -> None:
    path = os.path.join(markers_dir(), name)
    if os.path.exists(path):
        os.remove(path)


def _marker_line(name: str, label: str, *, active: bool) -> str:
    """One colorized marker row: [x]/[ ] name — description."""
    if active:
        box = utils.style_text("[x]", color=utils.AnsiColor.GREEN)
        name_c = utils.style_text(f"{name:<14}", color=utils.AnsiColor.CYAN, bold=True)
    else:
        box = utils.style_text("[ ]", color=utils.AnsiColor.GRAY)
        name_c = f"{name:<14}"
    dash = utils.style_text("—", color=utils.AnsiColor.GRAY)
    label_c = utils.style_text(label, color=utils.AnsiColor.GRAY)
    return f"  {box} {name_c} {dash} {label_c}"


def markers_report() -> str:
    """Human-readable list: known markers (checked) + active custom ones."""
    defs = load_marker_defs()
    state = markers_dir()
    lines = [utils.style_text(f"Markers ({state}):", bold=True)]
    lines.extend(
        _marker_line(name, defs[name].get("name", ""), active=has_marker(name))
        for name in sorted(defs, key=lambda n: (defs[n].get("order", 999), n))
    )
    if os.path.isdir(state):
        lines.extend(
            _marker_line(name, "custom", active=True)
            for name in sorted(os.listdir(state))
            if os.path.isfile(os.path.join(state, name)) and name not in defs
        )
    return "\n".join(lines)


# ---------------------------------------------------------------- env / os
def os_kind() -> str:
    system = platform.system()
    if system == PLATFORM_DARWIN:
        return "macos"
    if system == PLATFORM_WINDOWS:
        return "windows"
    if system in (PLATFORM_LINUX, "Android"):
        prefix = os.environ.get("PREFIX", "")
        if "com.termux" in prefix or os.environ.get("ANDROID_ROOT"):
            return "android"
        return "linux"
    return "linux"


def has_gui() -> bool:
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def hook_env(phase: str) -> dict[str, str]:
    """MACKUP_* contract exported to hook processes."""
    env = dict(os.environ)
    env.update(
        {
            "MACKUP_PHASE": phase,
            "MACKUP_ROLE": machine_role(),
            "MACKUP_OS": os_kind(),
            "MACKUP_ARCH": platform.machine(),
            "MACKUP_HAS_GUI": "1" if has_gui() else "0",
            "MACKUP_CONFIG_DIR": dirs.config_dir(),
            "MACKUP_DATA_DIR": dirs.data_dir(),
            "MACKUP_STATE_DIR": dirs.state_dir(),
            "MACKUP_BACKUP_DIR": backup_dir(),
            "MACKUP_MARKERS_DIR": dirs.markers_state_dir(),
            "MACKUP_DCONF_BACKUP_DIR": dirs.dconf_backup_dir(),
        },
    )
    return env
