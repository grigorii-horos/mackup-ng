"""
The applications database.

The Applications Database provides an easy to use interface to load application
data from the Mackup Database (files).
"""

import os
import platform
import re
import tomllib
from typing import ClassVar

from . import blocks, constants, utils
from .constants import APPS_DIR, CUSTOM_APPS_DIR, CUSTOM_APPS_DIR_XDG

# ${VAR} token; NAME is captured. Reserved names below are the dual-path
# built-ins; anything else is resolved from the environment / source_env.
_ENV_VAR_RE = re.compile(r"\$\{(\w+)\}")


class ApplicationsDatabase:
    """Database containing all the configured applications."""

    # Reserved built-in var names (dual-path, platform-aware) — NOT env vars.
    # The MACKUP_ prefix marks them as mackup-owned so they never clash with a
    # real ${VAR} the user resolves from the environment / source_env.
    _RESERVED_VARS: ClassVar[set[str]] = {
        "MACKUP_XDG_CONFIG",
        "MACKUP_XDG_DATA",
        "MACKUP_XDG_STATE",
        "MACKUP_XDG_CACHE",
    }
    _CROSS_PLATFORM_PATH_VARS: ClassVar[dict[str, dict[str, str]]] = {
        "${MACKUP_XDG_CONFIG}": {
            "linux": ".config",
            "mac": "Library/Application Support",
            "windows": "AppData/Roaming",
        },
        "${MACKUP_XDG_DATA}": {
            "linux": ".local/share",
            "mac": "Library/Application Support",
            "windows": "AppData/Local",
        },
        "${MACKUP_XDG_STATE}": {
            "linux": ".local/state",
            "mac": "Library/Application Support",
            "windows": "AppData/Local",
        },
        "${MACKUP_XDG_CACHE}": {
            "linux": ".cache",
            "mac": "Library/Caches",
            "windows": "AppData/Local",
        },
    }

    @staticmethod
    def _split_top_level_items(value: str) -> list[str]:
        """Split comma-separated items, honoring nested braces/selectors."""
        parts: list[str] = []
        current: list[str] = []
        brace_depth = 0
        bracket_depth = 0
        for char in value:
            if char == "," and brace_depth == 0 and bracket_depth == 0:
                parts.append("".join(current))
                current = []
                continue
            if char == "{":
                brace_depth += 1
            elif char == "}":
                brace_depth -= 1
            elif char == "[":
                bracket_depth += 1
            elif char == "]":
                bracket_depth -= 1
            current.append(char)
        parts.append("".join(current))
        return parts

    @classmethod
    def _resolve_platform_selectors_with_backup(cls, path: str) -> tuple[str, str]:
        """
        Resolve selectors returning (local_path_expr, backup_path_expr).

        Semantics for `[linux:...,mac:...,windows:...,fallback]`:
        - local path: selected platform branch, otherwise fallback
        - backup path: fallback (canonical path)

        If no fallback exists, backup path falls back to the selected path.
        """
        start = path.find("[")
        if start == -1:
            return (path, path)

        depth = 0
        end = -1
        for idx in range(start, len(path)):
            if path[idx] == "[":
                depth += 1
            elif path[idx] == "]":
                depth -= 1
                if depth == 0:
                    end = idx
                    break

        if end == -1:
            return (path, path)

        inner = path[start + 1:end]
        items = cls._split_top_level_items(inner)
        if len(items) <= 1:
            return (path, path)

        platform_alias = cls._current_platform_alias()
        platform_keys = {
            "linux": "linux",
            "lin": "linux",
            "mac": "mac",
            "macos": "mac",
            "osx": "mac",
            "darwin": "mac",
            "windows": "windows",
            "win": "windows",
        }

        selected: str | None = None
        fallback: str | None = None
        saw_selector_syntax = False

        for item in items:
            token = item.strip()
            if ":" in token:
                key, value = token.split(":", 1)
                norm_key = platform_keys.get(key.strip().lower())
                if norm_key is None:
                    continue
                saw_selector_syntax = True
                if norm_key == platform_alias and selected is None:
                    selected = value.strip()
            elif fallback is None:
                fallback = token

        if not saw_selector_syntax and fallback is None:
            return (path, path)

        local_replacement = selected if selected is not None else fallback
        if local_replacement is None:
            return (path, path)
        backup_replacement = fallback if fallback is not None else local_replacement

        prefix = path[:start]
        suffix = path[end + 1:]

        def join_parts(replacement: str) -> str:
            suffix_part = suffix
            if replacement.endswith("/") and suffix_part.startswith("/"):
                suffix_part = suffix_part[1:]
            return f"{prefix}{replacement}{suffix_part}"

        local_path = join_parts(local_replacement)
        backup_path = join_parts(backup_replacement)
        return (
            cls._resolve_platform_selectors_with_backup(local_path)[0],
            cls._resolve_platform_selectors_with_backup(backup_path)[1],
        )

    @staticmethod
    def _current_platform_alias() -> str:
        """Return normalized platform alias used by path selectors."""
        system_name = platform.system()
        if system_name == constants.PLATFORM_DARWIN:
            return "mac"
        if system_name == constants.PLATFORM_WINDOWS:
            return "windows"
        return "linux"

    @classmethod
    def _resolve_platform_selectors(cls, path: str) -> str:
        """
        Resolve platform-specific selectors in square brackets.

        Syntax:
            [linux:...,mac:...,windows:...,fallback]
        """
        return cls._resolve_platform_selectors_with_backup(path)[0]

    @classmethod
    def _expand_builtin_path_vars(
        cls, path: str, *, for_backup: bool = False,
    ) -> str:
        """
        Expand Mackup-specific built-in path variables.

        These are not environment variables; they are static aliases intended
        for Mackup application cfg files.
        """
        expanded = path
        platform_alias = "linux" if for_backup else cls._current_platform_alias()
        for token, mapping in cls._CROSS_PLATFORM_PATH_VARS.items():
            value = mapping.get(platform_alias)
            if value is not None:
                expanded = expanded.replace(token, value)
        return expanded

    @classmethod
    def _expand_braces(cls, path: str) -> set[str]:
        """
        Expand simple shell-like brace groups in a path.

        Example:
            .config/app/{a,b}.json -> {'.config/app/a.json', '.config/app/b.json'}

        If braces are unmatched or contain no top-level comma, the path is
        returned unchanged.
        """
        start = path.find("{")
        if start == -1:
            return {path}

        depth = 0
        end = -1
        for idx in range(start, len(path)):
            if path[idx] == "{":
                depth += 1
            elif path[idx] == "}":
                depth -= 1
                if depth == 0:
                    end = idx
                    break

        if end == -1:
            return {path}

        inner = path[start + 1:end]
        items = cls._split_top_level_items(inner)
        if len(items) <= 1:
            return {path}

        prefix = path[:start]
        suffix = path[end + 1:]
        expanded: set[str] = set()
        for item in items:
            for candidate in cls._expand_braces(f"{prefix}{item}{suffix}"):
                expanded.add(candidate)
        return expanded

    @classmethod
    def _expand_brace_mappings(
        cls, local_expr: str, backup_expr: str,
    ) -> set[tuple[str, str]]:
        """
        Expand braces for local/backup expressions while preserving mapping intent.
        """
        local_expanded = sorted(cls._expand_braces(local_expr))
        backup_expanded = sorted(cls._expand_braces(backup_expr))

        if len(local_expanded) == 1 and len(backup_expanded) == 1:
            return {(local_expanded[0], backup_expanded[0])}
        if len(local_expanded) == len(backup_expanded):
            return set(zip(local_expanded, backup_expanded, strict=True))
        if len(backup_expanded) == 1:
            return {(local_path, backup_expanded[0]) for local_path in local_expanded}
        if len(local_expanded) == 1:
            return {(local_expanded[0], backup_path) for backup_path in backup_expanded}

        raise ValueError(
            "Unable to pair brace expansions between local and backup paths: "
            f"{local_expr!r} vs {backup_expr!r}",
        )

    @staticmethod
    def _lookup_source_env(name: str, env_files: list[str]) -> str | None:
        """Return NAME's value from the first source_env file that defines it."""
        for env_file in env_files:
            path = os.path.expanduser(str(env_file))
            try:
                with open(path, encoding="utf-8") as handle:
                    for raw in handle:
                        line = raw.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        key, value = line.split("=", 1)
                        if key.strip() == name:
                            return value.strip().strip('"').strip("'")
            except OSError:
                continue
        return None

    @classmethod
    def _expand_env_vars(cls, path: str, env_files: list[str]) -> str:
        """Replace non-reserved ``${VAR}`` with env / source_env values.

        Reserved built-ins (``${MACKUP_*}``) are left untouched (resolved
        elsewhere). Raises ``KeyError`` if a referenced var is unresolved.
        """
        def repl(match: re.Match) -> str:
            name = match.group(1)
            if name in cls._RESERVED_VARS:
                return match.group(0)
            value = os.environ.get(name)
            if value is None:
                value = cls._lookup_source_env(name, env_files)
            if value is None:
                raise KeyError(name)
            return value

        return _ENV_VAR_RE.sub(repl, path)

    @classmethod
    def _entry_to_exprs(
        cls, entry: str, env_files: list[str] | None = None,
    ) -> tuple[str, str]:
        """Resolve a configuration_files entry into (local_expr, backup_expr).

        Local == backup layout; selectors, built-in vars and env vars all apply.
        """
        env_files = env_files or []
        local_expr, backup_expr = cls._resolve_platform_selectors_with_backup(entry)
        local_expr = cls._expand_env_vars(
            cls._expand_builtin_path_vars(local_expr), env_files,
        )
        backup_expr = cls._expand_env_vars(
            cls._expand_builtin_path_vars(backup_expr, for_backup=True), env_files,
        )
        return local_expr, backup_expr

    @classmethod
    def _pair_to_exprs(
        cls, src: str, dest: str, env_files: list[str] | None = None,
    ) -> tuple[str, str]:
        """Resolve an explicit [mapped_files] pair into (local_expr, backup_expr).

        SRC is the local path; DEST is where it lives in the backup folder.
        Selectors, built-in vars, env vars and braces are honored on each side.
        """
        env_files = env_files or []
        local_expr = cls._expand_env_vars(
            cls._expand_builtin_path_vars(
                cls._resolve_platform_selectors_with_backup(src)[0],
            ),
            env_files,
        )
        backup_expr = cls._expand_env_vars(
            cls._expand_builtin_path_vars(
                cls._resolve_platform_selectors_with_backup(dest)[1],
                for_backup=True,
            ),
            env_files,
        )
        return local_expr, backup_expr

    @classmethod
    def _register_exprs(
        cls,
        local_expr: str,
        backup_expr: str,
        files_set: set[str],
        mappings_set: set[tuple[str, str]],
    ) -> None:
        """Brace-expand, reject absolute paths, and record local/backup pairs."""
        for local_path, backup_path in cls._expand_brace_mappings(
            local_expr, backup_expr,
        ):
            if any(p.startswith("/") for p in (local_path, backup_path)):
                raise ValueError(
                    "Unsupported absolute path in mapping: "
                    f"{local_path!r} -> {backup_path!r}",
                )
            files_set.add(local_path)
            mappings_set.add((local_path, backup_path))

    def __init__(self) -> None:
        """Create a ApplicationsDatabase instance."""
        # Build the dict that will contain the properties of each application
        self.apps: dict[str, dict[str, str | set[str]]] = {}
        self.app_file_mappings: dict[str, set[tuple[str, str]]] = {}
        self.app_blocks: dict[str, list[dict]] = {}
        self.app_env_files: dict[str, list[str]] = {}

        for config_file in ApplicationsDatabase.get_config_files():
            with open(config_file, "rb") as handle:
                try:
                    data = tomllib.load(handle)
                except tomllib.TOMLDecodeError:
                    continue

            # The app id is the toml filename without the extension.
            filename: str = os.path.basename(config_file)
            app_name: str = filename[: -len(".toml")]

            # Config keys live flat at the top level. A legacy [application]
            # table is still accepted: its keys fall back for name/files/env.
            legacy = data.get("application")
            if not isinstance(legacy, dict):
                legacy = {}

            # Start building a dict for this app
            self.apps[app_name] = {}

            # Fancy display name (falls back to the id)
            self.apps[app_name]["name"] = data.get(
                "name", legacy.get("name", app_name),
            )

            # The whole top level is one block: top-level keys that are not
            # sync/meta become the top-level implicit block. It counts as a block
            # only if it carries an action sub-table (xml/copy/chmod/run/systemd);
            # it is prepended to the [[block]] array (so it runs first).
            reserved = {
                "name",
                "files",
                "configuration_files",
                "mapped_files",
                "source_env",
                "block",
                "application",
            }
            top_block = {k: v for k, v in data.items() if k not in reserved}
            cfg_blocks = list(data.get("block", []))
            if blocks.block_action(top_block) is not None:
                cfg_blocks.insert(0, top_block)
            self.app_blocks[app_name] = cfg_blocks

            # Extra ${VAR} beyond the built-ins resolve from these files (+ env).
            env_files = list(
                data.get("source_env", legacy.get("source_env", [])),
            )
            self.app_env_files[app_name] = env_files

            # Add the configuration files to sync
            config_files: set[str] = set()
            config_mappings: set[tuple[str, str]] = set()
            self.apps[app_name]["configuration_files"] = config_files
            self.app_file_mappings[app_name] = config_mappings

            config_paths = next(
                (
                    v
                    for v in (
                        data.get("files"),
                        legacy.get("files"),
                        data.get("configuration_files"),
                        legacy.get("configuration_files"),
                    )
                    if v is not None
                ),
                [],
            )
            for path in config_paths:
                try:
                    local_expr, backup_expr = self._entry_to_exprs(
                        str(path), env_files,
                    )
                except KeyError as exc:
                    print(utils.colorize_message(
                        f"Warning: {app_name}: unresolved var {exc} in {path!r}, "
                        "skipping",
                    ))
                    continue
                self._register_exprs(
                    local_expr, backup_expr, config_files, config_mappings,
                )
            for src, dest in data.get("mapped_files", {}).items():
                try:
                    local_expr, backup_expr = self._pair_to_exprs(
                        str(src), str(dest), env_files,
                    )
                except KeyError as exc:
                    print(utils.colorize_message(
                        f"Warning: {app_name}: unresolved var {exc} in {src!r}, "
                        "skipping",
                    ))
                    continue
                self._register_exprs(
                    local_expr, backup_expr, config_files, config_mappings,
                )

    @staticmethod
    def get_config_files() -> set[str]:
        """
        Return the application configuration files.

        Return a list of configuration files describing the apps supported by
        Mackup. The files returned are absolute full path to those files.
        e.g. /usr/lib/mackup/applications/bash.toml

        Only one config file per application should be returned, custom config
        having a priority over stock config. The ~/.mackup/applications/
        directory takes priority over the XDG location.

        Returns:
            set of strings.
        """
        # Configure the config parser
        apps_dir: str = os.path.join(
            os.path.dirname(os.path.realpath(__file__)), APPS_DIR,
        )

        # Custom apps directory: ~/.mackup/applications/
        legacy_custom_apps_dir: str = os.path.join(os.environ["HOME"], CUSTOM_APPS_DIR)

        # XDG custom apps directory: $XDG_CONFIG_HOME/mackup/applications/
        xdg_config_home: str = os.environ.get(
            "XDG_CONFIG_HOME", os.path.join(os.environ["HOME"], ".config"),
        )
        xdg_custom_apps_dir: str = os.path.join(xdg_config_home, CUSTOM_APPS_DIR_XDG)

        # List of stock application config files
        config_files: set[str] = set()

        # Temp list of user added app config file names
        custom_files: set[str] = set()

        # Get the list of custom application config files from legacy directory first
        # (legacy takes priority over XDG)
        if os.path.isdir(legacy_custom_apps_dir):
            for filename in os.listdir(legacy_custom_apps_dir):
                if filename.endswith(".toml"):
                    config_files.add(os.path.join(legacy_custom_apps_dir, filename))
                    custom_files.add(filename)

        # Get custom application config files from XDG directory
        # (only if not already in legacy directory)
        if os.path.isdir(xdg_custom_apps_dir):
            for filename in os.listdir(xdg_custom_apps_dir):
                if filename.endswith(".toml") and filename not in custom_files:
                    config_files.add(os.path.join(xdg_custom_apps_dir, filename))
                    custom_files.add(filename)

        # Add the default provided app config files, but only if those are not
        # customized, as we don't want to overwrite custom app config.
        for filename in os.listdir(apps_dir):
            if filename.endswith(".toml") and filename not in custom_files:
                config_files.add(os.path.join(apps_dir, filename))

        return config_files

    def get_name(self, name: str) -> str:
        """
        Return the fancy name of an application.

        Args:
            name (str)

        Returns:
            str
        """
        value = self.apps[name]["name"]
        assert isinstance(value, str)
        return value

    def get_files(self, name: str) -> set[str]:
        """
        Return the list of config files of an application.

        Args:
            name (str)

        Returns:
            set of str.
        """
        value = self.apps[name]["configuration_files"]
        assert isinstance(value, set)
        return value

    def get_file_mappings(self, name: str) -> set[tuple[str, str]]:
        """Return local->backup path mappings for an application."""
        return set(self.app_file_mappings[name])

    def get_blocks(self, name: str) -> list[dict]:
        """Return the config's action blocks in order (top-level block first)."""
        return list(self.app_blocks.get(name, []))

    def get_env_files(self, name: str) -> list[str]:
        """Return the config's source_env files (for ${VAR} in blocks)."""
        return list(self.app_env_files.get(name, []))

    def app_has_sync(self, name: str) -> bool:
        """True if the config declares files to sync (not a block-only config)."""
        return bool(self.apps.get(name, {}).get("configuration_files"))

    def get_app_names(self) -> set[str]:
        """
        Return application names.

        Return the list of application names that are available in the
        database.

        Returns:
            set of str.
        """
        app_names: set[str] = set()
        for name in self.apps:
            app_names.add(name)

        return app_names

    def get_pretty_app_names(self) -> set[str]:
        """
        Return the list of pretty app names that are available in the database.

        Returns:
            set of str.
        """
        pretty_app_names: set[str] = set()
        for app_name in self.get_app_names():
            pretty_app_names.add(self.get_name(app_name))

        return pretty_app_names
