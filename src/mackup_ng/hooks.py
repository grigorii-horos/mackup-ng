"""Machine-local markers and the MACKUP_* env contract for mackup-ng.

Layout under the Mackup home (``~/.mackup/``)::

    applications/   config .toml files: sync lists + action blocks (see blocks.py)
    markers/        LOCAL marker definitions (*.toml, same format as apps)
    dconf-backup/   dconf dumps (*.dconf)

Marker STATE (empty flag files toggling behaviour on this machine only, never
synced) lives in ``$XDG_STATE_HOME/mackup/markers/``. ``backup`` marks the
source machine.

Action blocks (`[run]`) receive a ``MACKUP_*`` environment contract via
:func:`hook_env`.
"""

import contextlib
import os
import platform
import shutil
import tomllib

from . import utils
from .config import Config
from .constants import (
    CUSTOM_MARKERS_DIR,
    DCONF_DIRNAME,
    LEGACY_MARKERS_STATE_DIR,
    MACKUP_HOME_DIR,
    MARKERS_DEFS_DIRNAME,
    MARKERS_STATE_XDG,
    PLATFORM_DARWIN,
    PLATFORM_LINUX,
    PLATFORM_WINDOWS,
)


# ---------------------------------------------------------------- paths
def mackup_home() -> str:
    """Absolute path to ~/.mackup/."""
    return os.path.join(os.environ["HOME"], MACKUP_HOME_DIR)


def backup_dir() -> str:
    """Absolute path to the storage folder Mackup syncs into (``Config.fullpath``).

    Resolves the configured storage engine/path from ``~/.mackup.cfg`` so hooks
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
    """Local marker definitions: ~/.mackup/markers/."""
    return os.path.join(os.environ["HOME"], CUSTOM_MARKERS_DIR)


def markers_dir() -> str:
    """Directory holding marker STATE flags: $XDG_STATE_HOME/mackup/markers/."""
    base = os.environ.get(
        "XDG_STATE_HOME", os.path.join(os.environ["HOME"], ".local", "state"),
    )
    return os.path.join(base, MARKERS_STATE_XDG)


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
    """Known marker definitions: package built-ins + ~/.mackup/markers/.

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


def _migrate_legacy_markers() -> None:
    """Move pre-XDG state flags from ~/.mackup/markers/ into the XDG state dir.

    That directory now also holds marker *definitions* (``*.toml``); only the
    extensionless flag files are migrated, definitions are left untouched.
    """
    legacy = os.path.join(os.environ["HOME"], LEGACY_MARKERS_STATE_DIR)
    if not os.path.isdir(legacy):
        return
    dst = markers_dir()
    if os.path.abspath(legacy) == os.path.abspath(dst):
        return  # defs dir == state dir (misconfigured); nothing to migrate
    for name in os.listdir(legacy):
        src = os.path.join(legacy, name)
        if name.endswith(".toml") or not os.path.isfile(src):
            continue  # keep definitions and subdirs
        os.makedirs(dst, exist_ok=True)
        if not os.path.exists(os.path.join(dst, name)):
            shutil.move(src, os.path.join(dst, name))
    with contextlib.suppress(OSError):
        os.rmdir(legacy)  # only succeeds when now empty (no defs, no flags)


def has_marker(name: str) -> bool:
    _migrate_legacy_markers()
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
    _migrate_legacy_markers()
    os.makedirs(markers_dir(), exist_ok=True)
    open(os.path.join(markers_dir(), name), "a").close()


def unset_marker(name: str) -> None:
    _migrate_legacy_markers()
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
    if system == PLATFORM_LINUX:
        prefix = os.environ.get("PREFIX", "")
        if "com.termux" in prefix or os.environ.get("ANDROID_ROOT"):
            return "android"
        return "linux"
    return "linux"


def has_gui() -> bool:
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def hook_env(phase: str) -> dict[str, str]:
    """MACKUP_* contract exported to hook processes."""
    home = mackup_home()
    env = dict(os.environ)
    env.update(
        {
            "MACKUP_PHASE": phase,
            "MACKUP_ROLE": machine_role(),
            "MACKUP_OS": os_kind(),
            "MACKUP_ARCH": platform.machine(),
            "MACKUP_HAS_GUI": "1" if has_gui() else "0",
            "MACKUP_CONFIG_DIR": home,
            "MACKUP_BACKUP_DIR": backup_dir(),
            "MACKUP_MARKERS_DIR": markers_dir(),
            "MACKUP_DCONF_BACKUP_DIR": os.path.join(home, DCONF_DIRNAME),
        },
    )
    return env
