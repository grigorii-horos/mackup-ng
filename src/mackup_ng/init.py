"""First-run setup: point mackup at a backup folder and write its config.

``mackup init <path>`` exists so the first sync on a new machine is not the
dangerous one. It writes the config, then the caller runs an ordinary sync
with the backup side given authority — see ``ApplicationProfile``'s
``prefer_backup``.
"""

from __future__ import annotations

import os
import tomllib

from . import dirs, utils


def resolve_backup_dir(path: str) -> str:
    """The value to store in ``storage.backup_dir`` for ``path``.

    ``path`` is read the way a person types it at a shell prompt: relative to
    the current directory, with ``~`` expanded. A folder inside ``$HOME`` is
    stored relative to it, because the config folder is itself synced and an
    absolute path would name the wrong place on a machine whose home differs.
    Anything outside ``$HOME`` is stored as given.
    """
    absolute = os.path.abspath(os.path.expanduser(path))
    home = os.path.abspath(os.environ["HOME"])
    if absolute == home:
        return absolute
    if absolute.startswith(os.path.join(home, "")):
        return os.path.relpath(absolute, home)
    return absolute


def write_config(path: str, dry_run: bool) -> str:
    """Create the config pointing at ``path``; return the resolved value.

    Refuses when a config already exists rather than overwriting one: `init`
    is a first-run command, and a stray one must not silently repoint a
    working setup at somewhere else.
    """
    config_path = dirs.config_file()
    if os.path.exists(config_path):
        current = _current_backup_dir(config_path)
        utils.error(
            f"A config already exists: {config_path}\n"
            f"It backs up into: {current}\n"
            "\n"
            "`init` sets up a new machine and will not overwrite it. Edit"
            " storage.backup_dir in that file to point somewhere else.",
        )

    absolute = os.path.abspath(os.path.expanduser(path))
    parent = os.path.dirname(absolute)
    if not os.path.isdir(parent):
        utils.error(
            f"Unable to find the folder that would contain it: {parent}\n"
            "\n"
            "mackup creates the backup folder itself, but not the whole tree"
            " above it — that would turn a typo into a new directory nobody"
            " asked for.",
        )

    backup_dir = resolve_backup_dir(path)
    if dry_run:
        print(
            utils.colorize_message(
                f"Would write {config_path} with backup_dir = {backup_dir!r}",
            ),
        )
        return backup_dir

    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    with open(config_path, "w") as handle:
        handle.write(
            "[storage]\n"
            f'backup_dir = "{backup_dir}"\n'
            "\n"
            "[applications]\n"
            "# Empty `sync` means every supported application.\n"
            "sync = []\n"
            "ignore = []\n",
        )
    print(utils.colorize_message(f"Wrote {config_path}"))
    return backup_dir


def _current_backup_dir(config_path: str) -> str:
    """The existing config's ``storage.backup_dir``, for the refusal message."""
    try:
        with open(config_path, "rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return "<unreadable>"
    storage = data.get("storage")
    if not isinstance(storage, dict):
        return "<unset>"
    value = storage.get("backup_dir")
    return str(value) if isinstance(value, str) else "<unset>"
