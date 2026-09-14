"""Package used to manage the ~/.config/mackup/config.toml file."""

import os
import os.path
import tomllib
from pathlib import Path

from . import dirs
from .constants import (
    ENGINE_DROPBOX,
    ENGINE_FS,
    ENGINE_GDRIVE,
    ENGINE_ICLOUD,
    MACKUP_BACKUP_PATH,
)
from .utils import (
    error,
    get_dropbox_folder_location,
    get_google_drive_folder_location,
    get_icloud_folder_location,
)


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

        self._data = self._load(self._best_config_path(filename))

        self._engine = self._parse_engine()
        self._path = self._parse_path()
        self._directory = self._parse_directory()
        self._apps_to_ignore = self._parse_apps_to_ignore()
        self._apps_to_sync = self._parse_apps_to_sync()

    @property
    def engine(self) -> str:
        """
        The engine used by the storage.

        ENGINE_DROPBOX, ENGINE_GDRIVE, ENGINE_ICLOUD or ENGINE_FS.

        Returns:
            str
        """
        return str(self._engine)

    @property
    def path(self) -> str:
        """
        Path to the Mackup configuration files.

        The path to the directory where Mackup is gonna create and store his
        directory.

        Returns:
            str
        """
        return str(self._path)

    @property
    def directory(self) -> str:
        """
        The name of the Mackup directory, named Mackup by default.

        Returns:
            str
        """
        return str(self._directory)

    @property
    def fullpath(self) -> str:
        """
        Full path to the Mackup configuration files.

        The full path to the directory when Mackup is storing the configuration
        files.

        Returns:
            str
        """
        return str(os.path.join(self.path, self.directory))

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

    def _parse_engine(self) -> str:
        """
        Parse the storage engine in the config.

        Returns:
            str
        """
        engine = self._table("storage").get("engine", ENGINE_DROPBOX)

        if not isinstance(engine, str):
            raise ConfigError(
                f"storage.engine must be a string, got {type(engine).__name__}",
            )

        if engine not in (ENGINE_DROPBOX, ENGINE_GDRIVE, ENGINE_ICLOUD, ENGINE_FS):
            raise ConfigError(f"Unknown storage engine: {engine}")

        return engine

    def _parse_path(self) -> str:
        """
        Parse the storage path in the config.

        Returns:
            str
        """
        if self.engine == ENGINE_DROPBOX:
            return get_dropbox_folder_location()
        if self.engine == ENGINE_GDRIVE:
            return get_google_drive_folder_location()
        if self.engine == ENGINE_ICLOUD:
            return get_icloud_folder_location()

        cfg_path = self._table("storage").get("path")
        if cfg_path is None:
            raise ConfigError(
                "The required 'path' can't be found while"
                " the 'file_system' engine is used.",
            )
        if not isinstance(cfg_path, str):
            raise ConfigError(
                f"storage.path must be a string, got {type(cfg_path).__name__}",
            )
        # An absolute cfg_path wins, which is what os.path.join already does.
        return os.path.join(os.environ["HOME"], cfg_path)

    def _parse_directory(self) -> str:
        """
        Parse the storage directory in the config.

        Returns:
            str
        """
        directory = self._table("storage").get("directory", MACKUP_BACKUP_PATH)
        if not isinstance(directory, str):
            raise ConfigError(
                f"storage.directory must be a string, "
                f"got {type(directory).__name__}",
            )
        return directory

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
