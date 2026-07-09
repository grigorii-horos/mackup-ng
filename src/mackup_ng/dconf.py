"""dconf backup/restore for mackup-ng (Linux/GNOME).

Tracked dconf paths are stored as ``*.dconf`` dump files under
``~/.mackup/dconf-backup/``. The file name encodes the path:
``/org/gnome/terminal/`` <-> ``org.gnome.terminal.dconf``.

On ``mackup sync``:
  * backup-role machine dumps each tracked path to its file (before the file
    sync, so the fresh dumps get synced out);
  * restore-role machine loads each file into dconf (after the file sync).

New paths are registered with ``mackup dconf-add <path>...``.
"""

import os
import platform
import re
import shutil
import subprocess

from . import utils
from .constants import DCONF_DIRNAME, MACKUP_HOME_DIR, PLATFORM_LINUX

# /org/gnome/terminal/ (trailing slash optional)
_VALID_PATH = re.compile(r"^/[A-Za-z0-9]([A-Za-z0-9_-]*/)*[A-Za-z0-9_-]*/?$")


def dconf_dir() -> str:
    return os.path.join(os.environ["HOME"], MACKUP_HOME_DIR, DCONF_DIRNAME)


def have_dconf() -> bool:
    return platform.system() == PLATFORM_LINUX and shutil.which("dconf") is not None


def path_to_file(path: str) -> str:
    """/a/b/c/ -> a.b.c"""
    return path.strip("/").replace("/", ".")


def file_to_path(name: str) -> str:
    """a.b.c -> /a/b/c/"""
    return "/" + name.replace(".", "/") + "/"


def valid_path(path: str) -> bool:
    return bool(_VALID_PATH.match(path))


def _tracked_files() -> list[str]:
    directory = dconf_dir()
    if not os.path.isdir(directory):
        return []
    return sorted(f for f in os.listdir(directory) if f.endswith(".dconf"))


def dump_all(dry_run: bool = False) -> None:
    """Dump every tracked dconf path to its ``*.dconf`` file."""
    if not have_dconf():
        return
    for filename in _tracked_files():
        path = file_to_path(filename[: -len(".dconf")])
        if not valid_path(path):
            print(utils.colorize_message(f"Skipping non-dconf file: {filename}"))
            continue
        full = os.path.join(dconf_dir(), filename)
        print(utils.colorize_message(f"Backing up dconf {path}"))
        if dry_run:
            continue
        result = subprocess.run(
            ["dconf", "dump", path], capture_output=True, text=True, check=False,
        )
        if result.returncode != 0 or not result.stdout:
            print(utils.colorize_message(f"Warning: empty/missing dconf {path}"))
            continue
        with open(full, "w") as handle:
            handle.write(result.stdout)


def load_all(dry_run: bool = False) -> None:
    """Load every tracked ``*.dconf`` file into dconf."""
    if not have_dconf():
        return
    for filename in _tracked_files():
        path = file_to_path(filename[: -len(".dconf")])
        if not valid_path(path):
            print(utils.colorize_message(f"Skipping non-dconf file: {filename}"))
            continue
        full = os.path.join(dconf_dir(), filename)
        # never `dconf load` an empty dump — it could wipe the path's keys
        if os.path.getsize(full) == 0:
            print(utils.colorize_message(f"Skipping empty dconf dump: {filename}"))
            continue
        print(utils.colorize_message(f"Restoring dconf {path}"))
        if dry_run:
            continue
        with open(full, "rb") as handle:
            result = subprocess.run(
                ["dconf", "load", path], stdin=handle, check=False,
            )
        if result.returncode != 0:
            print(utils.colorize_message(f"Warning: dconf load failed: {path}"))


def add(paths: list[str], dry_run: bool = False) -> int:
    """Register + immediately dump the given dconf path(s).

    Returns process exit code (0 on at least one valid path).
    """
    if not have_dconf():
        print(utils.colorize_message("Warning: dconf not available on this system"))
        return 1

    os.makedirs(dconf_dir(), exist_ok=True)
    added = 0
    for raw in paths:
        path = "/" + raw.strip("/") + "/"
        if not valid_path(path):
            print(
                utils.colorize_message(
                    f"Warning: ignoring '{raw}' — not a dconf path "
                    "(e.g. /org/gnome/terminal/)",
                ),
            )
            continue
        added += 1
        filename = path_to_file(path) + ".dconf"
        full = os.path.join(dconf_dir(), filename)
        if dry_run:
            print(utils.colorize_message(f"Backing up dconf {path} -> {filename}"))
            continue
        result = subprocess.run(
            ["dconf", "dump", path], capture_output=True, text=True, check=False,
        )
        with open(full, "w") as handle:
            handle.write(result.stdout or "")
        if result.stdout:
            print(utils.colorize_message(f"Backed up dconf {path} -> {filename}"))
        else:
            print(
                utils.colorize_message(
                    f"Warning: dconf {path} empty — file created, "
                    "will fill on next sync",
                ),
            )

    if added == 0:
        return 1
    return 0
