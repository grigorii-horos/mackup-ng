"""Package used to manage the ~/.config/mackup/config.toml file."""

import os
import os.path
import tomllib
from pathlib import Path
from typing import ClassVar

from . import dirs
from .constants import (
    LEGACY_CONFIG_FILE,
    LEGACY_HOME_DIR,
)
from .utils import colorize_message, error

_KNOWN_KEYS: dict[str, set[str]] = {
    "storage": {"backup_dir"},
    "applications": {"ignore", "sync"},
}

# A single process can build several Config() instances that all read the
# same file: the primary sync/apply flow builds one, and hooks.backup_dir()
# builds its own to resolve MACKUP_BACKUP_DIR — once per [copy]/[chmod]/[run]
# block. Without this, every one of them would re-print the same unknown-key
# warning; this makes it fire once per resolved config *path* instead of
# once per process — hooks.backup_dir() always reads the default location
# (it builds Config() with no filename) while the primary flow honours
# --config-file, so `mackup apply --config-file other.toml` legitimately
# reads two different files with two different sets of unknown keys in one
# run, and both must warn. A bare per-process bool collapsed that to "warn
# at most once ever", silently swallowing the second file's warning. A class
# (attribute assignment, not `global`) rather than a bare module variable —
# ruff's PLW0603 disallows rebinding a module global from inside a function.
# Tests that move $HOME/XDG_* or the config path must call
# _reset_unknown_key_warning() the way ignore.load_globs.cache_clear() is
# used, or a warning from an earlier test would be silently swallowed here.
class _UnknownKeyWarningState:
    warned_paths: ClassVar[set[str]] = set()


def _reset_unknown_key_warning() -> None:
    """Test hook: let the once-per-path warning fire again."""
    _UnknownKeyWarningState.warned_paths.clear()


class Config:
    """The Mackup Config class."""

    def __init__(self, filename: str | None = None) -> None:
        """
        Create a Config instance.

        Args:
            filename (str): Optional path to the config file. If empty,
                            defaults to dirs.config_file()
        """
        assert isinstance(filename, str) or filename is None

        self._reject_legacy_layout()

        config_path = self._best_config_path(filename)
        self._data = self._load(config_path)
        self._warn_on_unknown_keys(config_path)

        self._fullpath = self._parse_backup_dir()
        self._apps_to_ignore = self._parse_apps_to_ignore()
        self._apps_to_sync = self._parse_apps_to_sync()

    @property
    def fullpath(self) -> str:
        """
        Absolute path of the folder Mackup backs up into.

        Returns:
            str
        """
        return str(self._fullpath)

    @property
    def apps_to_ignore(self) -> set[str]:
        """
        Get the list of applications ignored in the config file.

        Returns:
            set. Set of application names to ignore, lowercase
        """
        return set(self._apps_to_ignore)

    @property
    def apps_to_sync(self) -> set[str]:
        """
        Get the list of applications allowed in the config file.

        Returns:
            set. Set of application names to allow, lowercase
        """
        return set(self._apps_to_sync)

    @staticmethod
    def _reject_legacy_layout() -> None:
        """Refuse to run while the pre-XDG layout is still in place.

        Stopping beats carrying on: a config left at the old path is not
        read at all, and the new one is required, so continuing would mean
        acting on a configuration the user thinks is in force but is not.
        """
        home = Path.home()
        for legacy in (home / LEGACY_CONFIG_FILE, home / LEGACY_HOME_DIR):
            if legacy.exists():
                error(
                    f"Legacy layout detected: {legacy}\n"
                    "\n"
                    "mackup-ng now reads TOML from the XDG directories:\n"
                    f"  config        {dirs.config_file()}\n"
                    f"  applications  {dirs.custom_apps_dir()}\n"
                    f"  ignores       {dirs.custom_ignores_dir()}\n"
                    f"  markers       {dirs.custom_markers_dir()}\n"
                    f"  dconf dumps   {dirs.dconf_backup_dir()}\n"
                    "\n"
                    "Move your files there, convert the config to TOML, and "
                    "remove the legacy path above.",
                )

    def _warn_on_unknown_keys(self, config_path: str) -> None:
        """Name anything we will not act on, once per resolved config path.

        A [colors] table sat in a real config being silently ignored for
        years; a typo such as applications.ignor fails the same silent way.

        A run can build several Config() instances from the *same* file (the
        primary sync/apply flow builds one; hooks.backup_dir() builds its own
        per action block to resolve MACKUP_BACKUP_DIR, always against the
        default location), and that file cannot change mid-run, so
        re-checking after the first instance for a given path would only
        ever reprint the same lines. But `--config-file` means the primary
        flow and hooks.backup_dir() can legitimately be reading *different*
        files in the same run, each with its own unknown keys to report —
        so the warning is keyed by path, not suppressed globally.
        """
        if config_path in _UnknownKeyWarningState.warned_paths:
            return
        warned = False
        for name, value in self._data.items():
            if name not in _KNOWN_KEYS:
                print(
                    colorize_message(
                        f"Warning: unknown config table [{name}], ignored",
                    ),
                )
                warned = True
                continue
            if not isinstance(value, dict):
                continue
            unknown = sorted(set(value) - _KNOWN_KEYS[name])
            if unknown:
                names = ", ".join(unknown)
                print(
                    colorize_message(
                        f"Warning: unknown key(s) in [{name}]: {names}, ignored",
                    ),
                )
                warned = True
        if warned:
            _UnknownKeyWarningState.warned_paths.add(config_path)

    @staticmethod
    def _reject_managed_fullpath(fullpath: str) -> None:
        """The storage folder must not sit inside a directory mackup manages.

        Replaces two hard-coded path-string comparisons; a containment check
        covers every managed directory and survives a rename.
        """
        target = os.path.realpath(fullpath)
        for managed in (dirs.config_dir(), dirs.data_dir(), dirs.state_dir()):
            managed_real = os.path.realpath(managed)
            if target == managed_real or target.startswith(managed_real + os.sep):
                raise ConfigError(
                    f"The storage directory '{fullpath}' is inside "
                    f"'{managed}', which mackup manages. "
                    "Choose another directory.",
                )

    @staticmethod
    def _load(path: str) -> dict:
        """Parse the config file, or return an empty mapping when absent."""
        if not os.path.isfile(path):
            return {}
        try:
            with open(path, "rb") as handle:
                return tomllib.load(handle)
        except tomllib.TOMLDecodeError as exc:
            error(f"cannot parse {path}: {exc}")
        except OSError as exc:
            error(f"cannot read {path}: {exc}")

    def _best_config_path(self, filename: str | None = None) -> str:
        """
        The config file to read: the explicit override, else the XDG location.

        Args:
            filename (str or None): Optional override, absolute or relative to
                the home directory.

        Returns:
            str: the absolute path to the config file
        """
        assert isinstance(filename, str) or filename is None

        if not filename:
            return dirs.config_file()

        config_path = Path(filename).expanduser()
        if not config_path.is_absolute():
            config_path = Path.home() / filename

        if not config_path.is_file():
            error(f"The config file '{config_path}' does not exist. Aborting.")

        try:
            config_path.relative_to(Path.home())
        except ValueError:
            error(
                f"The config file '{config_path}' is not in your home "
                "directory. Aborting.",
            )

        return str(config_path.absolute())

    def _table(self, name: str) -> dict:
        """One top-level table, or an empty mapping when it is absent."""
        value = self._data.get(name, {})
        if not isinstance(value, dict):
            raise ConfigError(
                f"[{name}] must be a table, got {type(value).__name__}",
            )
        return value

    def _string_list(self, table: str, key: str) -> set[str]:
        """A list-of-strings value as a set."""
        value = self._table(table).get(key, [])
        if not isinstance(value, list) or not all(
            isinstance(item, str) for item in value
        ):
            raise ConfigError(f"{table}.{key} must be a list of strings")
        return set(value)

    def _parse_backup_dir(self) -> str:
        """
        Parse the one storage location in the config.

        A relative value is resolved against $HOME; an absolute one is used
        as given. There is no default: without a backup folder there is
        nothing sensible to do, so an absent value is an error rather than a
        guess.

        Returns:
            str
        """
        backup_dir = self._table("storage").get("backup_dir")
        if backup_dir is None:
            raise ConfigError(
                "storage.backup_dir is required — set it to the folder Mackup"
                f" should back up into, in {dirs.config_file()}",
            )
        if not isinstance(backup_dir, str):
            raise ConfigError(
                f"storage.backup_dir must be a string,"
                f" got {type(backup_dir).__name__}",
            )
        # An absolute backup_dir wins, which is what os.path.join already does.
        fullpath = os.path.join(os.environ["HOME"], backup_dir)
        self._reject_managed_fullpath(fullpath)
        return fullpath

    def _parse_apps_to_ignore(self) -> set[str]:
        """
        Parse the applications to ignore in the config.

        Returns:
            set
        """
        return self._string_list("applications", "ignore")

    def _parse_apps_to_sync(self) -> set[str]:
        """
        Parse the applications to backup in the config.

        Returns:
            set
        """
        return self._string_list("applications", "sync")


class ConfigError(Exception):
    """Exception used for handle errors in the configuration."""
