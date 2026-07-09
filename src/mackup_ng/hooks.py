"""Machine-local hooks and markers for mackup-ng.

Layout under the Mackup home (``~/.mackup/``)::

    applications/   custom application .cfg files
    backup.d/       executables run BEFORE ``mackup sync``
    sets.d/         declarative config sets applied natively after the file sync
                    (see sets.py) — the restore-phase logic lives here now
    markers/        machine-local condition flags gating hooks
    state/          hook scratch space
    dconf-backup/   dconf dumps (*.dconf)

Markers are empty files whose presence toggles behaviour on this machine only
(they must not be synced). ``backup`` marks the source machine.

Hooks receive a ``MACKUP_*`` environment contract (via :func:`hook_env`); the
same contract is passed to ``[[run]]`` scripts inside config sets.
"""

import os
import platform
import subprocess

from . import utils
from .constants import (
    DCONF_DIRNAME,
    HOOKS_BACKUP_DIRNAME,
    HOOKS_RESTORE_DIRNAME,
    MACKUP_HOME_DIR,
    MARKERS_DIRNAME,
    PLATFORM_DARWIN,
    PLATFORM_LINUX,
    PLATFORM_WINDOWS,
    STATE_DIRNAME,
)

# Phase name -> hook sub-directory
PHASE_DIRS: dict[str, str] = {
    "backup": HOOKS_BACKUP_DIRNAME,
    "restore": HOOKS_RESTORE_DIRNAME,
}

# Known markers with descriptions (custom markers are also allowed)
KNOWN_MARKERS: dict[str, str] = {
    "backup": "this machine is the source; auto-mode = backup",
    "low-resource": "weak device: syncthing tuning (sets.d)",
    "no-linger": "opt-out: do NOT enable systemd linger (restore hook)",
    "no-apikey": "opt-out: do NOT write syncthing apikey (sets.d)",
    "no-dconf": "opt-out: do NOT back up / restore dconf on sync",
}
KNOWN_ORDER: list[str] = [
    "backup",
    "low-resource",
    "no-linger",
    "no-apikey",
    "no-dconf",
]


# ---------------------------------------------------------------- paths
def mackup_home() -> str:
    """Absolute path to ~/.mackup/."""
    return os.path.join(os.environ["HOME"], MACKUP_HOME_DIR)


def markers_dir() -> str:
    return os.path.join(mackup_home(), MARKERS_DIRNAME)


def state_dir() -> str:
    return os.path.join(mackup_home(), STATE_DIRNAME)


def phase_dir(phase: str) -> str:
    return os.path.join(mackup_home(), PHASE_DIRS[phase])


# ---------------------------------------------------------------- markers
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


def markers_report() -> str:
    """Human-readable list: known markers (checked) + active custom ones."""
    lines = [f"Markers ({markers_dir()}):"]
    for name in KNOWN_ORDER:
        flag = "[x]" if has_marker(name) else "[ ]"
        lines.append(f"  {flag} {name:<14} — {KNOWN_MARKERS[name]}")
    if os.path.isdir(markers_dir()):
        for name in sorted(os.listdir(markers_dir())):
            if not os.path.isfile(os.path.join(markers_dir(), name)):
                continue
            if name in KNOWN_MARKERS:
                continue
            lines.append(f"  [x] {name:<14} — custom")
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


def _has_gui() -> str:
    if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
        return "1"
    return "0"


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
            "MACKUP_HAS_GUI": _has_gui(),
            "MACKUP_CONFIG_DIR": home,
            "MACKUP_MARKERS_DIR": markers_dir(),
            "MACKUP_STATE_DIR": state_dir(),
            "MACKUP_DCONF_BACKUP_DIR": os.path.join(home, DCONF_DIRNAME),
        },
    )
    return env


# ---------------------------------------------------------------- runner
def _is_executable(path: str) -> bool:
    return os.path.isfile(path) and os.access(path, os.X_OK)


def run_hooks(phase: str, dry_run: bool = False) -> None:
    """Run executable hooks in ~/.mackup/<phase>.d/ sorted by name.

    A failing hook is warned about but never aborts the run.
    """
    hook_dir = phase_dir(phase)
    if not os.path.isdir(hook_dir):
        return

    env = hook_env(phase)
    for name in sorted(os.listdir(hook_dir)):
        path = os.path.join(hook_dir, name)
        if not _is_executable(path):
            continue
        print(utils.colorize_message(f"Synchronizing hook {phase}: {name}"), flush=True)
        if dry_run:
            continue
        try:
            subprocess.run([path], env=env, check=True)
        except subprocess.CalledProcessError as exc:
            print(
                utils.colorize_message(
                    f"Warning: hook {name} failed (code {exc.returncode})",
                ),
            )
        except OSError as exc:
            print(
                utils.colorize_message(f"Warning: hook {name} could not run: {exc}"),
            )
