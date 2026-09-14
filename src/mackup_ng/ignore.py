"""Names that must never travel between a home folder and the backup.

Storage backends drop their own bookkeeping inside the folders they sync.
Syncthing is the loud one: every concurrent edit leaves a
``*.sync-conflict-*`` copy next to the file, and each shared folder carries a
``.stfolder`` marker. Copying those around is worse than pointless — a fresh
conflict copy also makes its side look like the newest one, so it can beat a
real edit on the other machine.

Patterns live in ``<name>.toml`` files with an ``[ignore]`` table, shipped
with the package and supplemented by the XDG directory. A local file replaces
the built-in of the same name outright, so ``patterns = []`` in
``$XDG_CONFIG_HOME/mackup/ignores/syncthing.toml`` turns the built-in set off.
A single config can add its own patterns with a top-level ``ignore`` key.

mackup skips these names in both directions and never deletes them: they
belong to the tool that made them.
"""

from __future__ import annotations

import os
import tomllib
from fnmatch import fnmatch
from functools import lru_cache
from typing import TYPE_CHECKING

from . import dirs
from .constants import IGNORES_DIRNAME

if TYPE_CHECKING:
    from collections.abc import Callable

Globs = tuple[str, ...]


def _pkg_ignores_dir() -> str:
    """Built-in ignore definitions shipped inside the package."""
    here = os.path.dirname(os.path.realpath(__file__))
    return os.path.join(here, IGNORES_DIRNAME)


def _read_patterns(path: str) -> list[str] | None:
    """The ``[ignore] patterns`` of one file, or None when it is unusable."""
    try:
        with open(path, "rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return None
    table = data.get("ignore")
    if not isinstance(table, dict):
        return None
    patterns = table.get("patterns", [])
    if not isinstance(patterns, list):
        return None
    return [str(pattern) for pattern in patterns]


@lru_cache(maxsize=1)
def load_globs() -> Globs:
    """Every globally ignored pattern, built-ins first.

    Cached: the files cannot change inside a single run. Tests that move
    ``$HOME`` call ``load_globs.cache_clear()``.
    """
    by_name: dict[str, list[str]] = {}
    for directory in (_pkg_ignores_dir(), dirs.custom_ignores_dir()):
        if not os.path.isdir(directory):
            continue
        for filename in sorted(os.listdir(directory)):
            if not filename.endswith(".toml"):
                continue
            patterns = _read_patterns(os.path.join(directory, filename))
            if patterns is not None:
                # A local file of the same name replaces the built-in
                # wholesale, which is what makes an empty list an off switch.
                by_name[filename[: -len(".toml")]] = patterns
    return tuple(pattern for name in sorted(by_name) for pattern in by_name[name])


def is_ignored(globs: Globs, name: str) -> bool:
    """Whether a single file or directory name belongs to the sync backend."""
    return any(fnmatch(name, pattern) for pattern in globs)


def is_ignored_path(globs: Globs, relative_path: str) -> bool:
    """Whether any component of a relative path is ignored."""
    return any(
        is_ignored(globs, part)
        for part in relative_path.replace(os.sep, "/").split("/")
        if part
    )


def copytree_ignore(globs: Globs) -> Callable[[str, list[str]], set[str]]:
    """The ``ignore`` callable :func:`shutil.copytree` expects."""

    def ignore_names(directory: str, names: list[str]) -> set[str]:
        del directory  # the decision is per name, wherever the directory sits
        return {name for name in names if is_ignored(globs, name)}

    return ignore_names
