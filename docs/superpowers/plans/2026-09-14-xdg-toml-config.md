# XDG Layout + TOML Configuration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move every mackup-ng file from `~/.mackup` and `~/.mackup.cfg` into the XDG base directories, and replace the INI config with TOML.

**Architecture:** A new `dirs.py` becomes the single resolver for the three XDG bases; every other module asks it for paths instead of joining onto `$HOME` itself. `config.py` drops `configparser` for `tomllib`. The dual lookup in `appsdb.py` and `ignore.py` collapses to the XDG branch.

**Tech Stack:** Python 3.12+, `tomllib` (stdlib), pytest, uv.

**Spec:** `docs/superpowers/specs/2026-09-14-xdg-toml-config-design.md`

## Global Constraints

- Python 3.12+ (`requires-python = ">= 3.12"`). `tomllib.TOMLDecodeError.lineno` exists only from 3.14 — use `str(exc)`.
- No backwards compatibility. `configparser` must not appear anywhere when done.
- Warnings use the established pattern `print(utils.colorize_message("Warning: ..."))`. `utils` has no `warn()` helper; do not add one.
- Test command: `uv run pytest`. Full gate: `make check` (lint, ruff, mypy, ty, test).
- Work on branch `feat/xdg-toml-config`. Commit after every task.
- Task order matters: the legacy-layout rejection (Task 7) must land *after* the fixture directory `tests/fixtures/.mackup/` is gone (Task 3), or every test errors out.

---

### Task 1: `dirs.py` — single XDG resolver

**Files:**
- Create: `src/mackup_ng/dirs.py`
- Modify: `src/mackup_ng/constants.py`
- Test: `tests/test_dirs.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces: `dirs.config_dir()`, `dirs.data_dir()`, `dirs.state_dir()`, `dirs.config_file()`, `dirs.custom_apps_dir()`, `dirs.custom_ignores_dir()`, `dirs.custom_markers_dir()`, `dirs.markers_state_dir()`, `dirs.dconf_backup_dir()` — all `() -> str`, all absolute. New constants `MACKUP_DIRNAME = "mackup"`, `CONFIG_FILENAME = "config.toml"`, `LEGACY_CONFIG_FILE = ".mackup.cfg"`, `LEGACY_HOME_DIR = ".mackup"`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_dirs.py`:

```python
"""XDG base resolution: one implementation, one set of fallback rules."""

import os
import unittest

from mackup_ng import dirs


class TestDirs(unittest.TestCase):
    def setUp(self):
        self._orig = {
            key: os.environ.get(key)
            for key in ("HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME")
        }
        os.environ["HOME"] = "/home/tester"
        for key in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME"):
            os.environ.pop(key, None)

    def tearDown(self):
        for key, orig in self._orig.items():
            if orig is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = orig

    def test_defaults_when_unset(self):
        assert dirs.config_dir() == "/home/tester/.config/mackup"
        assert dirs.data_dir() == "/home/tester/.local/share/mackup"
        assert dirs.state_dir() == "/home/tester/.local/state/mackup"

    def test_absolute_env_value_is_honoured(self):
        os.environ["XDG_CONFIG_HOME"] = "/elsewhere/cfg"
        assert dirs.config_dir() == "/elsewhere/cfg/mackup"

    def test_empty_env_value_falls_back(self):
        # appsdb.py used .get(var, default) and built a path from "/" here,
        # while ignore.py used `or default`. One rule now.
        os.environ["XDG_CONFIG_HOME"] = ""
        assert dirs.config_dir() == "/home/tester/.config/mackup"

    def test_relative_env_value_falls_back(self):
        # The XDG spec says a relative base must be ignored.
        os.environ["XDG_STATE_HOME"] = "relative/state"
        assert dirs.state_dir() == "/home/tester/.local/state/mackup"

    def test_derived_paths(self):
        assert dirs.config_file() == "/home/tester/.config/mackup/config.toml"
        assert dirs.custom_apps_dir() == "/home/tester/.config/mackup/applications"
        assert dirs.custom_ignores_dir() == "/home/tester/.config/mackup/ignores"
        assert dirs.custom_markers_dir() == "/home/tester/.config/mackup/markers"
        assert dirs.markers_state_dir() == "/home/tester/.local/state/mackup/markers"
        assert (
            dirs.dconf_backup_dir()
            == "/home/tester/.local/share/mackup/dconf-backup"
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_dirs.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mackup_ng.dirs'`

- [ ] **Step 3: Add the new constants**

In `src/mackup_ng/constants.py`, add below `APPS_DIR`:

```python
# Name of the mackup directory inside each XDG base
MACKUP_DIRNAME: str = "mackup"

# Main config file, inside $XDG_CONFIG_HOME/mackup/
CONFIG_FILENAME: str = "config.toml"

# Pre-XDG locations, kept only to reject them with a helpful message
LEGACY_CONFIG_FILE: str = ".mackup.cfg"
LEGACY_HOME_DIR: str = ".mackup"
```

Leave the old constants in place for now — later tasks delete them as their consumers go away.

- [ ] **Step 4: Write `src/mackup_ng/dirs.py`**

```python
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_dirs.py -v`
Expected: PASS, 5 tests.

- [ ] **Step 6: Run the full suite to confirm nothing regressed**

Run: `uv run pytest`
Expected: PASS — `dirs.py` has no consumers yet.

- [ ] **Step 7: Commit**

```bash
git add src/mackup_ng/dirs.py src/mackup_ng/constants.py tests/test_dirs.py
git commit -m "feat(dirs): single resolver for the XDG base directories

The three modules that resolved XDG bases spelled the fallback two
different ways: .get(var, default) builds a path from / when the variable
is set but empty, while .get(var) or default does not. One implementation,
plus the spec's rule that a relative base is ignored."
```

---

### Task 2: `config.py` — TOML parsing and the new location

**Files:**
- Modify: `src/mackup_ng/config.py` (full rewrite of parsing internals)
- Modify: `src/mackup_ng/constants.py` (drop `MACKUP_CONFIG_FILE`)
- Delete: `tests/fixtures/*.cfg` (13 files) → recreate as `.toml`
- Modify: `tests/test_config.py`, `tests/test_config_file_option.py`, `tests/test_cli.py`, `tests/test_info.py`, `tests/test_ignore_sync.py`, `tests/test_rm_destinations.py`, `tests/test_config_conditions.py`

**Interfaces:**
- Consumes: `dirs.config_file()` from Task 1.
- Produces: `Config(filename: str | None = None)` with unchanged public properties — `engine: str`, `path: str`, `directory: str`, `fullpath: str`, `apps_to_ignore: set[str]`, `apps_to_sync: set[str]`. New private helpers `Config._load(path) -> dict`, `Config._table(name) -> dict`, `Config._string_list(table, key) -> set[str]`. `ConfigError` unchanged.

- [ ] **Step 1: Convert the fixtures to TOML**

Delete all 13 `tests/fixtures/*.cfg` and create these `.toml` files in their place. The names keep their stems so the tests' string references only change extension.

`tests/fixtures/mackup-empty.toml` — empty file.

`tests/fixtures/mackup-apps_to_ignore.toml`:
```toml
[applications]
# The INI fixture also exercised ';' inline comments; TOML has only '#'.
ignore = ["subversion", "sequel-pro", "sabnzbd"]  # inline comment
```

`tests/fixtures/mackup-apps_to_sync.toml`:
```toml
[applications]
sync = ["sabnzbd", "sublime-text-3", "x11"]
```

`tests/fixtures/mackup-apps_to_ignore_and_sync.toml`:
```toml
[applications]
sync = ["sabnzbd", "sublime-text-3", "x11", "vim"]
ignore = ["subversion", "sequel-pro", "sabnzbd"]
```

`tests/fixtures/mackup-engine-dropbox.toml`:
```toml
[storage]
engine = "dropbox"
directory = "some_weirld_name"
```

`tests/fixtures/mackup-engine-file_system.toml`:
```toml
[storage]
engine = "file_system"
path = "some/relative/folder"

[applications]
sync = ["sabnzbd", "sublime-text-3", "x11"]
```

`tests/fixtures/mackup-engine-file_system-absolute.toml`:
```toml
[storage]
engine = "file_system"
path = "/some/absolute/folder"
directory = "custom_folder"

[applications]
ignore = ["subversion", "sequel-pro"]
```

`tests/fixtures/mackup-engine-file_system-no_path.toml`:
```toml
[storage]
engine = "file_system"
directory = "Mackup"
```

`tests/fixtures/mackup-engine-google_drive.toml`:
```toml
[storage]
engine = "google_drive"

[applications]
ignore = ["subversion", "sequel-pro", "sabnzbd"]
sync = ["sabnzbd", "sublime-text-3", "x11"]
```

`tests/fixtures/mackup-engine-icloud.toml`:
```toml
[storage]
engine = "icloud"

[applications]
ignore = ["subversion", "sequel-pro", "sabnzbd"]
sync = ["sabnzbd", "sublime-text-3", "x11"]
```

`tests/fixtures/mackup-engine-unknown.toml`:
```toml
[storage]
engine = "unknown_engine"
```

Two fixtures are **deleted without replacement**:
- `mackup-envarcheck.cfg` — the `$MACKUP_CONFIG` lookup is gone.
- `mackup-old-config.cfg` — `[Allowed Applications]` / `[Ignored Applications]` cannot occur in TOML.

Also delete `tests/fixtures/xdg-config-home/mackup/mackup.cfg` (the old XDG config lookup is gone). Leave `tests/fixtures/xdg-config-home/mackup/applications/` alone — Task 3 handles it.

- [ ] **Step 2: Rewrite `tests/test_config.py`**

Replace the whole file:

```python
import os
import os.path
import unittest
from pathlib import Path

import pytest

from mackup_ng import dirs
from mackup_ng.config import Config, ConfigError
from mackup_ng.constants import (
    ENGINE_DROPBOX,
    ENGINE_FS,
    ENGINE_GDRIVE,
    ENGINE_ICLOUD,
)


class TestConfig(unittest.TestCase):
    def setUp(self):
        realpath = os.path.dirname(os.path.realpath(__file__))
        os.environ["HOME"] = os.path.join(realpath, "fixtures")
        for key in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME"):
            os.environ.pop(key, None)

    def test_config_default_location(self):
        config_path = Path(dirs.config_file())
        config_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            config_path.write_text('[storage]\ndirectory = "test_config_default"\n')
            assert Config().directory == "test_config_default"
        finally:
            config_path.unlink(missing_ok=True)

    def test_config_no_config(self):
        cfg = Config()

        assert cfg.engine == ENGINE_DROPBOX
        assert isinstance(cfg.path, str)
        assert cfg.directory == "Mackup"
        assert cfg.apps_to_ignore == set()
        assert cfg.apps_to_sync == set()

    def test_config_empty(self):
        cfg = Config("mackup-empty.toml")

        assert cfg.engine == ENGINE_DROPBOX
        assert cfg.directory == "Mackup"
        assert cfg.apps_to_ignore == set()
        assert cfg.apps_to_sync == set()

    def test_config_apps_to_ignore(self):
        cfg = Config("mackup-apps_to_ignore.toml")

        assert cfg.apps_to_ignore == {"subversion", "sequel-pro", "sabnzbd"}
        assert cfg.apps_to_sync == set()

    def test_config_apps_to_sync(self):
        cfg = Config("mackup-apps_to_sync.toml")

        assert cfg.apps_to_sync == {"sabnzbd", "sublime-text-3", "x11"}
        assert cfg.apps_to_ignore == set()

    def test_config_apps_to_ignore_and_sync(self):
        cfg = Config("mackup-apps_to_ignore_and_sync.toml")

        assert cfg.apps_to_ignore == {"subversion", "sequel-pro", "sabnzbd"}
        assert cfg.apps_to_sync == {"sabnzbd", "sublime-text-3", "x11", "vim"}

    def test_config_engine_dropbox(self):
        cfg = Config("mackup-engine-dropbox.toml")

        assert cfg.engine == ENGINE_DROPBOX
        assert cfg.directory == "some_weirld_name"

    def test_config_engine_file_system(self):
        cfg = Config("mackup-engine-file_system.toml")

        assert cfg.engine == ENGINE_FS
        assert cfg.path == os.path.join(os.environ["HOME"], "some/relative/folder")
        assert cfg.directory == "Mackup"
        assert cfg.fullpath == os.path.join(cfg.path, "Mackup")

    def test_config_engine_file_system_absolute(self):
        cfg = Config("mackup-engine-file_system-absolute.toml")

        assert cfg.engine == ENGINE_FS
        assert cfg.path == "/some/absolute/folder"
        assert cfg.directory == "custom_folder"

    def test_config_engine_file_system_no_path(self):
        with pytest.raises(ConfigError):
            Config("mackup-engine-file_system-no_path.toml")

    def test_config_engine_google_drive(self):
        cfg = Config("mackup-engine-google_drive.toml")

        assert cfg.engine == ENGINE_GDRIVE

    def test_config_engine_icloud(self):
        cfg = Config("mackup-engine-icloud.toml")

        assert cfg.engine == ENGINE_ICLOUD

    def test_config_engine_unknown(self):
        with pytest.raises(ConfigError):
            Config("mackup-engine-unknown.toml")
```

Note `test_config_engine_google_drive` / `_icloud` no longer assert on `path`: those engines probe the filesystem, and the previous file asserted only the engine for them too.

- [ ] **Step 3: Update `tests/test_config_file_option.py`**

Change the three fixture names to `.toml` and drop the `MACKUP_CONFIG` cleanup line:

```python
    def setUp(self):
        realpath = os.path.dirname(os.path.realpath(__file__))
        os.environ["HOME"] = os.path.join(realpath, "fixtures")
        os.environ.pop("XDG_CONFIG_HOME", None)

    def test_config_with_relative_path(self):
        cfg = Config("mackup-apps_to_ignore.toml")

        assert cfg.apps_to_ignore == {"subversion", "sequel-pro", "sabnzbd"}

    def test_config_with_absolute_path(self):
        abs_path = os.path.join(os.environ["HOME"], "mackup-apps_to_sync.toml")
        cfg = Config(abs_path)

        assert cfg.apps_to_sync == {"sabnzbd", "sublime-text-3", "x11"}

    def test_mackup_with_config_file(self):
        mckp = Mackup("mackup-empty.toml")

        assert isinstance(mckp.mackup_folder, str)
```

Leave `test_mackup_without_config_file` and `test_config_file_does_not_exist` as they are.

- [ ] **Step 4: Convert the inline config writes in the remaining five test modules**

Each of these writes an INI config into a temp `$HOME`. In every one, point the path at `$XDG_CONFIG_HOME/mackup/config.toml` (creating the parent) and write TOML. The temp homes already set `XDG_CONFIG_HOME`; where they do not, set it to `os.path.join(home, ".config")` in `setUp`.

`tests/test_cli.py` (around line 39):
```python
        self.config_path = os.path.join(
            self.test_home, ".config", "mackup", "config.toml",
        )
        os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
        with open(self.config_path, "w") as f:
            f.write("[storage]\n")
            f.write('engine = "file_system"\n')
            f.write(f'path = "{self.test_storage}"\n')
            f.write('directory = "Mackup"\n')
            f.write("\n")
            f.write("[applications]\n")
            f.write('sync = ["test-app"]\n')
```

`tests/test_config_conditions.py` (around line 30):
```python
        self.config_path = os.path.join(
            self.home, ".config", "mackup", "config.toml",
        )
        os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
        with open(self.config_path, "w") as handle:
            handle.write(
                '[storage]\nengine = "file_system"\n'
                f'path = "{self.storage}"\ndirectory = "Mackup"\n\n'
                "[applications]\n"
                'sync = ["aaa-base", "zzz-override", "gated-blocks"]\n',
            )
```

`tests/test_ignore_sync.py` (around line 34):
```python
        self.config_path = os.path.join(
            self.test_home, ".config", "mackup", "config.toml",
        )
        os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
        self._write_config(["notes"])
```
with a helper on the class, because line 145 currently *appends* `other\n` to the INI file — appending a bare line to a TOML array is not possible, so that test must rewrite the whole config:
```python
    def _write_config(self, apps):
        entries = ", ".join(f'"{app}"' for app in apps)
        with open(self.config_path, "w") as handle:
            handle.write(
                '[storage]\nengine = "file_system"\n'
                f'path = "{self.test_storage}"\ndirectory = "Mackup"\n\n'
                f"[applications]\nsync = [{entries}]\n",
            )
```
and at the old line 145, replace the append with `self._write_config(["notes", "other"])`.

`tests/test_info.py` (around line 32) and `tests/test_rm_destinations.py` (around line 31): same move — build the path under `.config/mackup/config.toml`, `os.makedirs` the parent, and emit `[storage]` / `[applications]` TOML with quoted values. `test_info.py` already has a `write_config(apps)` helper; change its body only.

- [ ] **Step 5: Run the tests to verify they fail**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL — the INI parser cannot read TOML; engine comes back as `dropbox` where `file_system` is expected, and `apps_to_ignore` is empty.

- [ ] **Step 6: Rewrite `src/mackup_ng/config.py`**

Replace the imports and every private method; the public properties are untouched.

```python
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
```

Keep the six `@property` blocks (`engine`, `path`, `directory`, `fullpath`, `apps_to_ignore`, `apps_to_sync`) exactly as they are today.

Then the private half:

```python
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
```

The `CUSTOM_APPS_DIR` guard that used to live in `_parse_directory` is deliberately absent here — Task 7 reintroduces it as a containment check.

- [ ] **Step 7: Drop the dead constant**

In `src/mackup_ng/constants.py`, delete `MACKUP_CONFIG_FILE`. Verify nothing references it:

Run: `grep -rn "MACKUP_CONFIG_FILE\|configparser" src/ tests/`
Expected: no output.

- [ ] **Step 8: Run the tests to verify they pass**

Run: `uv run pytest tests/test_config.py tests/test_config_file_option.py tests/test_cli.py tests/test_info.py tests/test_ignore_sync.py tests/test_rm_destinations.py tests/test_config_conditions.py -v`
Expected: PASS.

Run: `uv run pytest`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add -A src/mackup_ng/config.py src/mackup_ng/constants.py tests/
git commit -m "feat(config): read TOML from the XDG config directory

configparser is gone. The config file is ~/.config/mackup/config.toml;
the \$MACKUP_CONFIG and XDG mackup.cfg lookups are removed, leaving the
default path and --config-file. Section-with-bare-keys lists become
arrays under [applications]. The public Config API is unchanged."
```

---

### Task 3: `appsdb.py` — one custom applications directory

**Files:**
- Modify: `src/mackup_ng/appsdb.py:546-586` (`get_config_files`)
- Modify: `src/mackup_ng/constants.py` (drop `CUSTOM_APPS_DIR`, `CUSTOM_APPS_DIR_XDG`)
- Move: `tests/fixtures/.mackup/applications/` → `tests/fixtures/.config/mackup/applications/`
- Modify: `tests/test_appsdb_xdg.py`

**Interfaces:**
- Consumes: `dirs.custom_apps_dir()` from Task 1.
- Produces: `ApplicationsDatabase.get_config_files() -> list[str]` — unchanged signature, two-tier precedence (stock, then custom).

- [ ] **Step 1: Move the fixture directory**

```bash
mkdir -p tests/fixtures/.config/mackup
git mv tests/fixtures/.mackup/applications tests/fixtures/.config/mackup/applications
rmdir tests/fixtures/.mackup
```

`tests/fixtures/.config/mackup/applications/` now holds `legacy-test-app.toml` and `priority-test-app.toml`. Rename the first to reflect reality:

```bash
git mv tests/fixtures/.config/mackup/applications/legacy-test-app.toml \
       tests/fixtures/.config/mackup/applications/custom-test-app.toml
```

- [ ] **Step 2: Rewrite `tests/test_appsdb_xdg.py`**

The file exists to prove legacy-over-XDG precedence, which no longer exists. Replace its test methods with:

```python
    def test_custom_apps_dir_is_found(self):
        """A profile in $XDG_CONFIG_HOME/mackup/applications is picked up."""
        config_files = ApplicationsDatabase.get_config_files()
        filenames = {os.path.basename(f) for f in config_files}

        assert "custom-test-app.toml" in filenames

    def test_custom_shadows_stock_of_the_same_name(self):
        """A custom file replaces the stock file with the same name."""
        config_files = ApplicationsDatabase.get_config_files()
        matching = [
            f for f in config_files
            if os.path.basename(f) == "priority-test-app.toml"
        ]

        assert len(matching) == 1
        assert matching[0].startswith(dirs.custom_apps_dir())

    def test_custom_files_come_last(self):
        """Custom files sort after stock files, so they win on conflicts."""
        config_files = ApplicationsDatabase.get_config_files()
        custom = [
            i for i, f in enumerate(config_files)
            if f.startswith(dirs.custom_apps_dir())
        ]
        stock = [
            i for i, f in enumerate(config_files)
            if not f.startswith(dirs.custom_apps_dir())
        ]

        assert custom, "no custom files discovered"
        assert min(custom) > max(stock)
```

Add `from mackup_ng import dirs` to the imports. Keep `setUp`/`tearDown` as they are — with `XDG_CONFIG_HOME` popped, `dirs.custom_apps_dir()` resolves to `tests/fixtures/.config/mackup/applications`, which is where the fixtures now live.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_appsdb_xdg.py -v`
Expected: FAIL — `get_config_files` still looks in `~/.mackup/applications`, so no custom file is found.

- [ ] **Step 4: Rewrite `get_config_files`**

In `src/mackup_ng/appsdb.py`, change the import line 15 to:

```python
from . import dirs
from .constants import APPS_DIR
```

and replace the method body:

```python
    @staticmethod
    def get_config_files() -> list[str]:
        """
        Return the application configuration files in precedence order.

        Stock files ship in the package; custom files live in
        ``$XDG_CONFIG_HOME/mackup/applications``. Later files win when two
        configs claim the same destination, and a custom file shadows a stock
        file of the same name entirely.

        Returns:
            list of absolute paths, weakest first.
        """
        apps_dir: str = os.path.join(
            os.path.dirname(os.path.realpath(__file__)),
            APPS_DIR,
        )
        custom_apps_dir: str = dirs.custom_apps_dir()

        def toml_names(directory: str) -> set[str]:
            if not os.path.isdir(directory):
                return set()
            return {name for name in os.listdir(directory) if name.endswith(".toml")}

        custom_names = toml_names(custom_apps_dir)
        stock_names = toml_names(apps_dir) - custom_names

        return [
            *(os.path.join(apps_dir, name) for name in sorted(stock_names)),
            *(os.path.join(custom_apps_dir, name) for name in sorted(custom_names)),
        ]
```

- [ ] **Step 5: Update the other tests' custom-apps paths**

`tests/test_config_conditions.py`, `tests/test_ignore_sync.py`, `tests/test_info.py` and `tests/test_rm_destinations.py` each build `os.path.join(home, ".mackup", "applications")`. Change every one to:

```python
        self.apps_dir = os.path.join(home, ".config", "mackup", "applications")
```

(using each file's own variable for the temp home). Run `grep -rn '"\.mackup"' tests/` and fix every hit.

- [ ] **Step 6: Drop the dead constants**

In `src/mackup_ng/constants.py`, delete `CUSTOM_APPS_DIR` and `CUSTOM_APPS_DIR_XDG`.

Run: `grep -rn "CUSTOM_APPS_DIR" src/ tests/`
Expected: no output.

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add -A src/mackup_ng tests/
git commit -m "feat(appsdb): one custom applications directory

The XDG directory and ~/.mackup/applications were both searched, with a
three-way name-shadowing computation to keep them apart. Only the XDG
directory remains."
```

---

### Task 4: `ignore.py` — one ignores directory

**Files:**
- Modify: `src/mackup_ng/ignore.py:29,43-50,77`
- Modify: `src/mackup_ng/constants.py` (drop `CUSTOM_IGNORES_DIR`, `IGNORES_DIR_XDG`)
- Test: `tests/test_ignore.py`

**Interfaces:**
- Consumes: `dirs.custom_ignores_dir()` from Task 1.
- Produces: nothing new. `_custom_ignores_dirs()` is deleted; the call site uses `dirs.custom_ignores_dir()` directly.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_ignore.py`:

```python
def test_custom_ignores_come_from_the_xdg_directory(tmp_path, monkeypatch):
    """An ignore definition in $XDG_CONFIG_HOME/mackup/ignores is honoured."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    ignores_dir = tmp_path / ".config" / "mackup" / "ignores"
    ignores_dir.mkdir(parents=True)
    (ignores_dir / "zz-custom.toml").write_text(
        '[ignore]\npatterns = ["*.customsuffix"]\n',
    )

    from mackup_ng import ignore

    # load_globs is lru_cached; a test that moves $HOME must clear it.
    ignore.load_globs.cache_clear()
    try:
        assert "*.customsuffix" in ignore.load_globs()
    finally:
        ignore.load_globs.cache_clear()
```

The public loader is `load_globs() -> Globs` (line 70), decorated with
`@lru_cache(maxsize=1)`; its own docstring says tests that move `$HOME` must
call `load_globs.cache_clear()`.

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_ignore.py -k customsuffix -v`
Expected: FAIL — with `XDG_CONFIG_HOME` unset, `_custom_ignores_dirs()` builds
the XDG path as `$HOME/.config/mackup/ignores`, which *does* resolve, so this
test may pass before the change. If it passes, say so and proceed: Step 3 still
removes the `~/.mackup/ignores` branch, and the test then guards the single
remaining directory against regression.

- [ ] **Step 3: Collapse the two directories into one**

In `src/mackup_ng/ignore.py`, change the constants import on line 29 to:

```python
from . import dirs
from .constants import IGNORES_DIRNAME
```

Delete `_custom_ignores_dirs()` (lines 43-50) entirely, and change line 77 from:

```python
    for directory in (_pkg_ignores_dir(), *_custom_ignores_dirs()):
```

to:

```python
    for directory in (_pkg_ignores_dir(), dirs.custom_ignores_dir()):
```

- [ ] **Step 4: Drop the dead constants**

In `src/mackup_ng/constants.py`, delete `CUSTOM_IGNORES_DIR` and `IGNORES_DIR_XDG`. Keep `IGNORES_DIRNAME` — both `ignore.py` and `dirs.py` use it.

Run: `grep -rn "CUSTOM_IGNORES_DIR\|IGNORES_DIR_XDG" src/ tests/`
Expected: no output.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_ignore.py tests/test_ignore_sync.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/mackup_ng/ignore.py src/mackup_ng/constants.py tests/test_ignore.py
git commit -m "feat(ignore): one ignores directory

_custom_ignores_dirs() returned ~/.mackup/ignores and the XDG directory;
only the latter remains, so the helper collapses into its call site."
```

---

### Task 5: `hooks.py` — markers via `dirs`, legacy migration removed

**Files:**
- Modify: `src/mackup_ng/hooks.py:1-15` (docstring), `:22-35` (imports), `:38-41`, `:64-76`, `:111-135` (migration), `:214-231` (`hook_env`)
- Modify: `src/mackup_ng/constants.py` (drop `MACKUP_HOME_DIR`, `CUSTOM_MARKERS_DIR`, `MARKERS_STATE_XDG`, `LEGACY_MARKERS_STATE_DIR`)
- Test: `tests/test_markers.py`

**Interfaces:**
- Consumes: `dirs.custom_markers_dir()`, `dirs.markers_state_dir()`, `dirs.config_dir()`, `dirs.data_dir()`, `dirs.state_dir()`, `dirs.dconf_backup_dir()` from Task 1.
- Produces: `hooks.custom_markers_dir() -> str`, `hooks.markers_dir() -> str` (both kept, now thin wrappers), `hooks.hook_env(phase: str) -> dict[str, str]` with two new keys `MACKUP_DATA_DIR` and `MACKUP_STATE_DIR`. `hooks.mackup_home()` and `hooks._migrate_legacy_markers()` cease to exist.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_markers.py`:

```python
def test_hook_env_exposes_the_three_xdg_roots(tmp_path, monkeypatch):
    """The MACKUP_* contract names each XDG root explicitly."""
    monkeypatch.setenv("HOME", str(tmp_path))
    for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME"):
        monkeypatch.delenv(var, raising=False)

    from mackup_ng import dirs, hooks

    env = hooks.hook_env("pre")

    assert env["MACKUP_CONFIG_DIR"] == dirs.config_dir()
    assert env["MACKUP_DATA_DIR"] == dirs.data_dir()
    assert env["MACKUP_STATE_DIR"] == dirs.state_dir()
    assert env["MACKUP_MARKERS_DIR"] == dirs.markers_state_dir()
    assert env["MACKUP_DCONF_BACKUP_DIR"] == dirs.dconf_backup_dir()


def test_no_legacy_marker_migration(tmp_path, monkeypatch):
    """Flags left in the pre-XDG directory are not resurrected."""
    monkeypatch.setenv("HOME", str(tmp_path))
    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME"):
        monkeypatch.delenv(var, raising=False)
    legacy = tmp_path / ".mackup" / "markers"
    legacy.mkdir(parents=True)
    (legacy / "backup").touch()

    from mackup_ng import hooks

    assert hooks.has_marker("backup") is False
    assert (legacy / "backup").exists()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_markers.py -k "xdg_roots or legacy" -v`
Expected: FAIL — `KeyError: 'MACKUP_DATA_DIR'` on the first; the second fails because `_migrate_legacy_markers` moves the flag and `has_marker` returns True.

- [ ] **Step 3: Rewrite the paths and the env contract**

In `src/mackup_ng/hooks.py`:

Replace the module docstring's layout block:

```python
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

Marker STATE (empty flag files toggling behaviour on this machine only, never
synced) is the only part that is machine-local. ``backup`` marks the source
machine.

Action blocks (`[run]`) receive a ``MACKUP_*`` environment contract via
:func:`hook_env`.
"""
```

Change the imports to:

```python
from . import dirs, utils
from .config import Config
from .constants import (
    MARKERS_DEFS_DIRNAME,
    PLATFORM_DARWIN,
    PLATFORM_LINUX,
    PLATFORM_WINDOWS,
)
```

Delete `mackup_home()` (lines 38-41). Replace the two directory helpers:

```python
def custom_markers_dir() -> str:
    """Local marker definitions: $XDG_CONFIG_HOME/mackup/markers/."""
    return dirs.custom_markers_dir()


def markers_dir() -> str:
    """Marker STATE flags: $XDG_STATE_HOME/mackup/markers/."""
    return dirs.markers_state_dir()
```

Update `backup_dir()`'s docstring — it says "from ``~/.mackup.cfg``"; make it "from the config file".

Delete `_migrate_legacy_markers()` (lines 111-132) and its three call sites at lines 135, 152 and 158. The `contextlib` and `shutil` imports become unused if nothing else needs them — check with `grep -n "contextlib\.\|shutil\." src/mackup_ng/hooks.py` and drop whichever import is dead.

Rewrite `hook_env`:

```python
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
```

- [ ] **Step 4: Fix `tests/test_markers.py` setUp paths**

The existing `setUp` builds `local_defs` under `~/.mackup/markers`. Change it to `os.path.join(home, ".config", "mackup", "markers")`, and the state directory to `os.path.join(home, ".local", "state", "mackup", "markers")`. Delete any test asserting the legacy migration moved flags.

Run `grep -n "\.mackup" tests/test_markers.py` and fix every hit.

- [ ] **Step 5: Drop the dead constants**

In `src/mackup_ng/constants.py`, delete `MACKUP_HOME_DIR`, `CUSTOM_MARKERS_DIR`, `MARKERS_STATE_XDG` and `LEGACY_MARKERS_STATE_DIR`. Keep `MARKERS_DIRNAME` and `MARKERS_DEFS_DIRNAME`.

Run: `grep -rn "MACKUP_HOME_DIR\|CUSTOM_MARKERS_DIR\|MARKERS_STATE_XDG\|LEGACY_MARKERS_STATE_DIR" src/ tests/`
Expected: only `dconf.py` still importing `MACKUP_HOME_DIR` — Task 6 fixes that. If so, do Task 6 before committing, or leave the constant and delete it there.

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_markers.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/mackup_ng/hooks.py src/mackup_ng/constants.py tests/test_markers.py
git commit -m "feat(hooks): markers and the env contract on the XDG roots

mackup_home() and the pre-XDG state migration are removed: there is no
legacy directory left to migrate from. hook_env gains MACKUP_DATA_DIR and
MACKUP_STATE_DIR so each root is addressable."
```

---

### Task 6: `dconf.py` — dumps in the data directory

**Files:**
- Modify: `src/mackup_ng/dconf.py:22,28-29`
- Modify: `src/mackup_ng/constants.py` (drop `MACKUP_HOME_DIR` if Task 5 left it)
- Test: `tests/test_dirs.py` (already covers the path); add a `dconf` assertion

**Interfaces:**
- Consumes: `dirs.dconf_backup_dir()` from Task 1.
- Produces: `dconf.dconf_dir() -> str` — unchanged signature, new location.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_dirs.py`:

```python
    def test_dconf_module_uses_the_data_directory(self):
        from mackup_ng import dconf

        assert dconf.dconf_dir() == dirs.dconf_backup_dir()
        assert dconf.dconf_dir() == "/home/tester/.local/share/mackup/dconf-backup"
```

Add `from mackup_ng import dirs` at the top of the file if it is not already imported that way.

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_dirs.py -k dconf -v`
Expected: FAIL — `dconf_dir()` returns `/home/tester/.mackup/dconf-backup`.

- [ ] **Step 3: Point `dconf.py` at the data directory**

In `src/mackup_ng/dconf.py`, lines 21-22 currently read:

```python
from . import utils
from .constants import DCONF_DIRNAME, MACKUP_HOME_DIR, PLATFORM_LINUX
```

Replace them with:

```python
from . import dirs, utils
from .constants import PLATFORM_LINUX
```

and replace `dconf_dir`:

```python
def dconf_dir() -> str:
    return dirs.dconf_backup_dir()
```

`DCONF_DIRNAME` is no longer needed in this module; `dirs.py` owns it.

- [ ] **Step 4: Drop the last dead constant**

In `src/mackup_ng/constants.py`, delete `MACKUP_HOME_DIR` if still present.

Run: `grep -rn "MACKUP_HOME_DIR" src/ tests/`
Expected: no output.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/mackup_ng/dconf.py src/mackup_ng/constants.py tests/test_dirs.py
git commit -m "feat(dconf): store dumps in \$XDG_DATA_HOME/mackup

dconf dumps are generated content, not configuration."
```

---

### Task 7: `config.py` — legacy rejection, validation, containment guard

**Files:**
- Modify: `src/mackup_ng/config.py`
- Test: `tests/test_config_errors.py` (create)

**Interfaces:**
- Consumes: `dirs.*` from Task 1; `Config` internals from Task 2.
- Produces: `Config._reject_legacy_layout()`, `Config._warn_on_unknown_keys()`, `Config._reject_managed_fullpath(fullpath)` — all private, no external callers.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_config_errors.py`:

```python
"""Config rejects the legacy layout and bad values loudly, not silently."""

import pytest

from mackup_ng.config import Config, ConfigError


def _xdg_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME"):
        monkeypatch.delenv(var, raising=False)
    config_dir = tmp_path / ".config" / "mackup"
    config_dir.mkdir(parents=True)
    return config_dir


def test_legacy_config_file_is_rejected(tmp_path, monkeypatch):
    _xdg_home(tmp_path, monkeypatch)
    (tmp_path / ".mackup.cfg").write_text("[storage]\nengine = dropbox\n")

    with pytest.raises(SystemExit) as excinfo:
        Config()

    assert "config.toml" in str(excinfo.value)


def test_legacy_home_directory_is_rejected(tmp_path, monkeypatch):
    _xdg_home(tmp_path, monkeypatch)
    (tmp_path / ".mackup").mkdir()

    with pytest.raises(SystemExit) as excinfo:
        Config()

    assert "config.toml" in str(excinfo.value)


def test_malformed_toml_names_the_file(tmp_path, monkeypatch):
    config_dir = _xdg_home(tmp_path, monkeypatch)
    (config_dir / "config.toml").write_text("[storage\nengine = ")

    with pytest.raises(SystemExit) as excinfo:
        Config()

    assert "config.toml" in str(excinfo.value)


def test_ignore_must_be_a_list(tmp_path, monkeypatch):
    config_dir = _xdg_home(tmp_path, monkeypatch)
    (config_dir / "config.toml").write_text('[applications]\nignore = "ssh"\n')

    with pytest.raises(ConfigError, match="applications.ignore"):
        Config()


def test_unknown_table_warns_but_parses(tmp_path, monkeypatch, capsys):
    config_dir = _xdg_home(tmp_path, monkeypatch)
    (config_dir / "config.toml").write_text(
        '[colors]\nfilename_path_separator = "1;32"\n'
        '\n[applications]\nignore = ["ssh"]\n',
    )

    cfg = Config()

    assert cfg.apps_to_ignore == {"ssh"}
    assert "colors" in capsys.readouterr().out


def test_unknown_key_warns(tmp_path, monkeypatch, capsys):
    config_dir = _xdg_home(tmp_path, monkeypatch)
    (config_dir / "config.toml").write_text('[applications]\nignor = ["ssh"]\n')

    Config()

    assert "ignor" in capsys.readouterr().out


def test_storage_directory_inside_a_managed_dir_is_rejected(tmp_path, monkeypatch):
    config_dir = _xdg_home(tmp_path, monkeypatch)
    (config_dir / "config.toml").write_text(
        '[storage]\nengine = "file_system"\n'
        'path = ".config/mackup"\ndirectory = "applications"\n',
    )

    with pytest.raises(ConfigError, match="manages"):
        Config()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_config_errors.py -v`
Expected: FAIL — no legacy rejection, no validation, no warnings, no containment guard.

- [ ] **Step 3: Add the legacy rejection**

In `src/mackup_ng/config.py`, extend the constants import with `LEGACY_CONFIG_FILE` and `LEGACY_HOME_DIR`, add `colorize_message` to the `utils` import, and add to `__init__` as the **first** statement after the assert:

```python
        self._reject_legacy_layout()
```

then the method:

```python
    @staticmethod
    def _reject_legacy_layout() -> None:
        """Refuse to run while the pre-XDG layout is still in place.

        Falling back silently would be worse than stopping: with no config
        found, the storage engine defaults to dropbox and a sync would write
        to the wrong place entirely.
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
```

- [ ] **Step 4: Add the unknown-key warning**

Add the module-level table near the imports:

```python
_KNOWN_KEYS: dict[str, set[str]] = {
    "storage": {"engine", "path", "directory"},
    "applications": {"ignore", "sync"},
}
```

Call it in `__init__` right after `self._data = ...`:

```python
        self._warn_on_unknown_keys()
```

```python
    def _warn_on_unknown_keys(self) -> None:
        """Name anything we will not act on.

        A [colors] table sat in a real config being silently ignored for
        years; a typo such as applications.ignor fails the same silent way.
        """
        for name, value in self._data.items():
            if name not in _KNOWN_KEYS:
                print(
                    colorize_message(
                        f"Warning: unknown config table [{name}], ignored",
                    ),
                )
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
```

- [ ] **Step 5: Add the containment guard**

In `_parse_directory`, after the `isinstance` check and before the return:

```python
        self._reject_managed_fullpath(os.path.join(self.path, directory))
```

and the method:

```python
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
```

`_parse_directory` runs after `_parse_path` in `__init__`, so `self.path` is available.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_config_errors.py -v`
Expected: PASS, 7 tests.

Run: `uv run pytest`
Expected: PASS. If `tests/fixtures/.mackup` still exists, every fixture-home test now fails — confirm Task 3 removed it.

- [ ] **Step 7: Commit**

```bash
git add src/mackup_ng/config.py tests/test_config_errors.py
git commit -m "feat(config): reject the legacy layout, validate types, warn on typos

Stopping is better than the silent alternative: with no config found the
engine defaults to dropbox and a sync writes to the wrong place. The
storage-directory guard becomes a containment check instead of two
hard-coded path strings."
```

---

### Task 8: self-sync profile

**Files:**
- Modify: `src/mackup_ng/applications/mackup.toml`
- Modify: `doc/.mackup/mackup.toml` → moved in Task 9
- Test: `tests/test_appsdb_blocks.py` or a new assertion in `tests/test_dirs.py`

**Interfaces:**
- Consumes: nothing.
- Produces: the `mackup` application profile syncing `.config/mackup` and `.local/share/mackup`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_self_sync_profile.py`:

```python
"""The Mackup profile syncs its own config and data, never its state."""

import os
import tomllib


def _profile() -> dict:
    here = os.path.dirname(os.path.realpath(__file__))
    path = os.path.join(
        here, "..", "src", "mackup_ng", "applications", "mackup.toml",
    )
    with open(path, "rb") as handle:
        return tomllib.load(handle)


def test_profile_syncs_config_and_data():
    files = _profile()["files"]

    assert ".config/mackup" in files
    assert ".local/share/mackup" in files


def test_profile_never_syncs_machine_local_state():
    """Syncing marker flags would carry the `backup` role to another machine
    and invert the direction of its next sync."""
    files = _profile()["files"]

    assert not any(f.startswith(".local/state") for f in files)
    assert ".mackup" not in files
    assert ".mackup.cfg" not in files
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_self_sync_profile.py -v`
Expected: FAIL — the profile still lists `.mackup.cfg` and `.mackup`.

- [ ] **Step 3: Rewrite the profile**

`src/mackup_ng/applications/mackup.toml`:

```toml
name = "Mackup"
files = [
    ".config/mackup",
    ".local/share/mackup",
]
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_self_sync_profile.py -v`
Expected: PASS.

Run: `uv run pytest`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mackup_ng/applications/mackup.toml tests/test_self_sync_profile.py
git commit -m "feat(apps): sync mackup's own XDG config and data directories

State stays out of the list on purpose: marker flags describe this
machine, and syncing them would carry the backup role across."
```

---

### Task 9: documentation

**Files:**
- Modify: `doc/README.md`, `doc/configuration_merge_guide.md`, `doc/ARCHITECTURE.md`, `README.md`, `src/mackup_ng/main.py:50`
- Move: `doc/.mackup.cfg` → `doc/config.toml`; `doc/.mackup/` → `doc/config/`

**Interfaces:**
- Consumes: the final behaviour from Tasks 1-8.
- Produces: no code.

- [ ] **Step 1: Replace the sample config**

```bash
git rm doc/.mackup.cfg
git mv doc/.mackup doc/config
```

Create `doc/config.toml`:

```toml
# Sample mackup-ng configuration file.
# Install it at ~/.config/mackup/config.toml

[storage]
# Where mackup stores your configuration files.
#   "dropbox"      — mackup locates your Dropbox folder itself
#   "google_drive" — likewise for Google Drive
#   "icloud"       — likewise for iCloud Drive
#   "file_system"  — you give the path, mackup detects nothing
engine = "dropbox"

# Required for "file_system", ignored otherwise. Relative paths are taken
# from your home directory; absolute paths are used as given.
# path = "some/folder/in/your/home"

# The folder mackup creates inside the storage. Defaults to "Mackup".
# directory = "Mackup"

[applications]
# Applications to sync. An empty list means every supported application.
# Run `mackup list` for the names.
sync = []

# Applications never to sync. Wins over `sync`.
ignore = ["ssh", "adium"]
```

Update `doc/config/mackup.toml` to match the profile from Task 8.

- [ ] **Step 2: Update the prose**

In `doc/README.md`, replace the configuration-location section. The old text lists four search paths; the new text is:

```markdown
All the configuration is done in a file named `config.toml` stored in
`$XDG_CONFIG_HOME/mackup/` (`~/.config/mackup/` unless you set the variable).

```bash
vi ~/.config/mackup/config.toml
```

`mackup --config-file=<path>` reads a different file instead; the path may be
absolute or relative to your home directory, and must lie inside it.
```

Replace every remaining `.mackup.cfg` mention with `config.toml` and the INI
snippets with the TOML equivalents (`[applications_to_sync]` + bare names
becomes `[applications]` with `sync = [...]`). Files to sweep:
`doc/README.md`, `doc/configuration_merge_guide.md`, `doc/ARCHITECTURE.md`,
`README.md`, and the usage docstring at `src/mackup_ng/main.py:50`.

In `README.md`, also add `MACKUP_DATA_DIR` and `MACKUP_STATE_DIR` to the
environment-contract list (around line 1096) and update the directory layout
description near line 15.

- [ ] **Step 3: Verify no stale references remain**

Run: `grep -rn "\.mackup\.cfg\|applications_to_sync\|applications_to_ignore\|~/\.mackup" --include='*.md' --include='*.py' --include='*.toml' . --exclude-dir=.venv --exclude-dir=.git --exclude-dir=dist --exclude-dir=docs`
Expected: no output. (`docs/superpowers/` is excluded — the historical specs and plans legitimately describe the old layout.)

- [ ] **Step 4: Run the docs linter and the suite**

Run: `make check`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A doc README.md src/mackup_ng/main.py
git commit -m "docs: XDG layout and TOML configuration"
```

---

### Task 10: migrate the author's live data

**Files:** none in the repo. This is a runbook executed once, by hand, against `$HOME`.

**Interfaces:**
- Consumes: the finished implementation from Tasks 1-9.
- Produces: a working installation on the new layout.

- [ ] **Step 1: Take a safety copy**

```bash
cp -a ~/.mackup /tmp/mackup-backup-$(date +%Y%m%d)
cp -a ~/.mackup.cfg /tmp/mackup-backup-$(date +%Y%m%d).cfg
```

Note: `cp` is aliased to `cp -i` in this shell; use `command cp -a` if the alias interferes.

- [ ] **Step 2: Create the new directories**

```bash
mkdir -p ~/.config/mackup ~/.local/share/mackup ~/.local/state/mackup/markers
```

- [ ] **Step 3: Move configuration**

```bash
mv ~/.mackup/applications ~/.config/mackup/applications
mv ~/.mackup/markers/*.toml ~/.config/mackup/markers/ 2>/dev/null || \
  mkdir -p ~/.config/mackup/markers
[ -d ~/.mackup/ignores ] && mv ~/.mackup/ignores ~/.config/mackup/ignores
```

- [ ] **Step 4: Move state and data**

```bash
mv ~/.mackup/markers/backup ~/.local/state/mackup/markers/ 2>/dev/null
mv ~/.mackup/markers/low-resource ~/.local/state/mackup/markers/ 2>/dev/null
mv ~/.mackup/dconf-backup ~/.local/share/mackup/dconf-backup
```

These two extensionless flags are still sitting in the pre-XDG directory; the
automatic migration never moved them.

- [ ] **Step 5: Write the converted config**

`~/.config/mackup/config.toml`:

```toml
[storage]
engine = "file_system"
path = "Sync/Configs"

[applications]
ignore = [
    "apple-music", "bash", "colorsync", "dolphin", "git", "insomnia",
    "iterm", "iterm2", "libreoffice", "macosx", "nosqlbooster-for-mongodb",
    "oh-my-zsh", "quicklook", "rectangle", "scripts", "ssh",
    "sublime-text", "sublime-text-2", "sublime-text-3", "terminal",
    "vlc", "vscode", "vscode-insiders", "vscode-oss", "verdaccio",
    "xcode",
]
sync = []
```

Cross-check the list against `~/.mackup.cfg` before deleting it — the old file
had 21 entries under `[applications_to_ignore]`. The `[colors]` section is
dropped: no code ever read it.

- [ ] **Step 6: Move the non-mackup files and delete the dead ones**

```bash
mv ~/.mackup/CLAUDE.md ~/.mackup/GEMINI.md ~/.mackup/patch.py ~/.config/mackup/
mv ~/.mackup/.codex ~/.config/mackup/
rm -rf ~/.mackup/sets.d ~/.mackup/backup.d ~/.mackup/state
```

`sets.d` holds 7 `*.toml` files that have been dead since `sets.apply_dir` was
removed in the unified-config-blocks change; `backup.d` and `state` are empty.

Update the first line of `~/.config/mackup/CLAUDE.md`, which names the old path.

- [ ] **Step 7: Verify before the point of no return**

```bash
cd ~/Projects/GitHub/grigorii-horos/mackup && uv run mackup list | head
uv run mackup info ~/.config/mackup/config.toml
```

Expected: the application list renders, and `info` reports the config file as
managed. If `mackup` still errors about a legacy layout, `~/.mackup` is not yet
empty — find what is left with `find ~/.mackup`.

- [ ] **Step 8: Remove the legacy paths**

```bash
rm -rf ~/.mackup ~/.mackup.cfg
```

- [ ] **Step 9: Clean the sync storage and re-sync**

```bash
ls ~/Sync/Configs/Mackup/ | grep -i mackup
rm -rf ~/Sync/Configs/Mackup/.mackup ~/Sync/Configs/Mackup/.mackup.cfg
uv run mackup sync
```

The stale copies would otherwise diverge from the new paths forever.

- [ ] **Step 10: Confirm the new layout is synced**

```bash
ls -la ~/.config/mackup ~/.local/share/mackup
```

Expected: both are symlinks into `~/Sync/Configs/Mackup/`, and
`~/.local/state/mackup` is a real directory that was **not** symlinked.

---

## Self-Review

**Spec coverage.** Target layout → Tasks 1, 3-6. Config format → Task 2.
`dirs.py` → Task 1. Module changes → Tasks 2-6. Self-sync profile → Task 8.
Hook env contract → Task 5. Error handling → Task 7. Migration runbook →
Task 10. Tests → distributed through every task. Documentation → Task 9.
No spec section is unimplemented.

**Type consistency.** `dirs.markers_state_dir()` is the state directory
everywhere; `dirs.custom_markers_dir()` is the definitions directory.
`hooks.markers_dir()` keeps its old name and now wraps
`dirs.markers_state_dir()`, matching its existing callers. `Config._table`,
`Config._string_list` and `Config._load` are introduced in Task 2 and reused
unchanged in Task 7.

**Known ordering constraint.** Task 7's legacy rejection fires on any
`~/.mackup`, including `tests/fixtures/.mackup/`, which Task 3 removes. Running
Task 7 before Task 3 turns the whole suite red. This is called out in Global
Constraints and in Task 7 Step 6.
