"""XDG base directory resolution for mackup-ng.

Every mackup-ng path derives from one of three XDG bases. Resolving them in
one place keeps the fallback identical everywhere: an environment value is
honoured only when it is non-empty *and* absolute, which is what the XDG base
directory specification requires. Before this module the rule was spelled two
different ways, so an empty ``XDG_CONFIG_HOME`` meant ``~/.config`` in
``ignore.py`` but ``/`` in ``appsdb.py``.
"""

from __future__ import annotations

import os

from .constants import (
    APPS_DIR,
    CONFIG_FILENAME,
    DCONF_DIRNAME,
    IGNORES_DIRNAME,
    MACKUP_DIRNAME,
    MARKERS_DIRNAME,
)


def _base(var: str, *default_parts: str) -> str:
    """The XDG base for ``var``, else ``default_parts`` joined onto $HOME."""
    value = os.environ.get(var, "")
    if value and os.path.isabs(value):
        return value
    return os.path.join(os.environ["HOME"], *default_parts)


def config_dir() -> str:
    """$XDG_CONFIG_HOME/mackup/ — everything the user edits. Synced."""
    return os.path.join(_base("XDG_CONFIG_HOME", ".config"), MACKUP_DIRNAME)


def data_dir() -> str:
    """$XDG_DATA_HOME/mackup/ — generated content. Synced."""
    return os.path.join(_base("XDG_DATA_HOME", ".local", "share"), MACKUP_DIRNAME)


def state_dir() -> str:
    """$XDG_STATE_HOME/mackup/ — machine-local state. Never synced."""
    return os.path.join(_base("XDG_STATE_HOME", ".local", "state"), MACKUP_DIRNAME)


def config_file() -> str:
    """The main config file."""
    return os.path.join(config_dir(), CONFIG_FILENAME)


def custom_apps_dir() -> str:
    """Local application profiles (*.toml)."""
    return os.path.join(config_dir(), APPS_DIR)


def custom_ignores_dir() -> str:
    """Local ignore definitions (*.toml)."""
    return os.path.join(config_dir(), IGNORES_DIRNAME)


def custom_markers_dir() -> str:
    """Local marker DEFINITIONS (*.toml)."""
    return os.path.join(config_dir(), MARKERS_DIRNAME)


def markers_state_dir() -> str:
    """Marker STATE flags — extensionless files, machine-local."""
    return os.path.join(state_dir(), MARKERS_DIRNAME)


def dconf_backup_dir() -> str:
    """dconf dumps (*.dconf)."""
    return os.path.join(data_dir(), DCONF_DIRNAME)
