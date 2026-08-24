"""Resolving a user-supplied path against the managed destinations.

`rm` and `info` both take a path the way a person types it — absolute,
`~`-prefixed, or relative to the current directory — and have to decide which
managed destination it means, or whether it sits *inside* a managed directory.
That resolution lives here so both read the same rules.
"""

from __future__ import annotations

import os

from .application import ApplicationProfile


def escapes_home(rel_path: str) -> bool:
    """Whether a relative path points outside the home folder."""
    return rel_path == ".." or rel_path.startswith(("../", "..\\", "/"))


def candidates(path: str) -> list[str]:
    """Home-relative readings of ``path``, most literal first."""
    found = [ApplicationProfile.normalize_relative_path(path)]
    if not os.path.isabs(os.path.expanduser(path)):
        absolute_path = os.path.abspath(path)
        home = os.path.abspath(os.environ["HOME"])
        try:
            cwd_relative = ApplicationProfile.normalize_relative_path(
                os.path.relpath(absolute_path, home),
            )
        except ValueError:
            cwd_relative = None
        # The current-directory interpretation is only a convenience for
        # running from inside a managed folder. When the current directory is
        # outside home it escapes and must be dropped, or it would wrongly
        # trip the unmanaged-path guard even though the literal candidate is a
        # valid managed home-relative path.
        if cwd_relative is not None and not escapes_home(cwd_relative):
            found.append(cwd_relative)
    return list(dict.fromkeys(found))


def is_managed_directory(
    mackup_folder: str, local_filename: str, backup_filename: str,
) -> bool:
    """Whether either side of a mapping currently exists as a directory."""
    return os.path.isdir(
        os.path.join(os.environ["HOME"], local_filename),
    ) or os.path.isdir(
        os.path.join(mackup_folder, backup_filename),
    )


def managed_descendant_relative(
    mackup_folder: str,
    requested_path: str,
    local_filename: str,
    backup_filename: str,
) -> str | None:
    """The path of ``requested_path`` below a managed directory, or None."""
    local_root = ApplicationProfile.normalize_relative_path(local_filename)
    try:
        relative_path = os.path.relpath(requested_path, local_root)
    except ValueError:
        return None

    if relative_path == os.curdir or relative_path.startswith(os.pardir + os.sep):
        return None
    if os.path.isabs(relative_path):
        return None
    if not is_managed_directory(mackup_folder, local_filename, backup_filename):
        return None

    return relative_path
