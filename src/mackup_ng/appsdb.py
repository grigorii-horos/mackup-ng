"""
The applications database.

The Applications Database provides an easy to use interface to load application
data from the Mackup Database (files).
"""

import configparser
import os
import platform
from typing import ClassVar

from . import constants
from .constants import APPS_DIR, CUSTOM_APPS_DIR, CUSTOM_APPS_DIR_XDG


class ApplicationsDatabase:
    """Database containing all the configured applications."""

    _PATH_SECTIONS: ClassVar[set[str]] = {"configuration_files"}
    _CROSS_PLATFORM_PATH_VARS: ClassVar[dict[str, dict[str, str]]] = {
        "@CONFIG@": {
            "linux": ".config",
            "mac": "Library/Application Support",
            "windows": "AppData/Roaming",
        },
        "@DATA@": {
            "linux": ".local/share",
            "mac": "Library/Application Support",
            "windows": "AppData/Local",
        },
        "@STATE@": {
            "linux": ".local/state",
            "mac": "Library/Application Support",
            "windows": "AppData/Local",
        },
        "@CACHE@": {
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
    def _read_path_entries_from_section(
        cls,
        config_file: str,
        section: str,
    ) -> list[str]:
        """
        Read raw path entries from a cfg section.

        This bypasses ConfigParser limitations for entries that begin with '[',
        which we use for platform selectors.
        """
        entries: list[str] = []
        current_section: str | None = None
        with open(config_file, encoding="utf-8") as f:
            for raw_line in f:
                stripped = raw_line.strip()
                if not stripped or stripped.startswith(("#", ";")):
                    continue

                if stripped.startswith("[") and stripped.endswith("]"):
                    section_name = stripped[1:-1].strip()
                    # Selector lines can also start/end with brackets; only treat as
                    # a new section if it looks like a section header.
                    is_header = (
                        section_name
                        and ":" not in section_name
                        and "," not in section_name
                        and "/" not in section_name
                        and "{" not in section_name
                        and "}" not in section_name
                    )
                    if is_header:
                        current_section = section_name
                        continue

                if current_section != section:
                    continue

                # Keep parity with existing no-value option semantics: path lines only.
                if "=" in stripped:
                    continue
                entries.append(stripped)
        return entries

    @classmethod
    def _read_sanitized_config_text_for_parser(cls, config_file: str) -> str:
        """
        Return cfg text sanitized for ConfigParser.

        Path entries in path sections may start with '[' due to platform
        selectors; ConfigParser interprets them as section headers. We replace
        such lines with placeholders for parser consumption only.
        """
        output: list[str] = []
        current_section: str | None = None
        placeholder_index = 0

        with open(config_file, encoding="utf-8") as f:
            for raw_line in f:
                stripped = raw_line.strip()

                if stripped.startswith("[") and stripped.endswith("]"):
                    section_name = stripped[1:-1].strip()
                    is_header = (
                        section_name
                        and ":" not in section_name
                        and "," not in section_name
                        and "/" not in section_name
                        and "{" not in section_name
                        and "}" not in section_name
                    )
                    if is_header:
                        current_section = section_name
                        output.append(raw_line)
                        continue

                if (
                    current_section in cls._PATH_SECTIONS
                    and stripped
                    and not stripped.startswith("#")
                    and not stripped.startswith(";")
                    and "=" not in stripped
                ):
                    placeholder_index += 1
                    output.append(f"__mackup_path_placeholder_{placeholder_index}__\n")
                    continue

                output.append(raw_line)

        return "".join(output)

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

    def __init__(self) -> None:
        """Create a ApplicationsDatabase instance."""
        # Build the dict that will contain the properties of each application
        self.apps: dict[str, dict[str, str | set[str]]] = {}
        self.app_file_mappings: dict[str, set[tuple[str, str]]] = {}

        for config_file in ApplicationsDatabase.get_config_files():
            config: configparser.ConfigParser = configparser.ConfigParser(
                allow_no_value=True,
            )

            # Needed to not lowercase the configuration_files in the ini files
            config.optionxform = str  # type: ignore

            config_text = self._read_sanitized_config_text_for_parser(config_file)
            config.read_string(config_text, source=config_file)
            if config.has_section("application"):
                # Get the filename without the directory name
                filename: str = os.path.basename(config_file)
                # The app name is the cfg filename with the extension
                app_name: str = filename[: -len(".cfg")]

                # Start building a dict for this app
                self.apps[app_name] = {}

                # Add the fancy name for the app, for display purpose
                app_pretty_name: str = config.get("application", "name")
                self.apps[app_name]["name"] = app_pretty_name

                # Add the configuration files to sync
                config_files: set[str] = set()
                config_mappings: set[tuple[str, str]] = set()
                self.apps[app_name]["configuration_files"] = config_files
                self.app_file_mappings[app_name] = config_mappings
                if config.has_section("configuration_files"):
                    for path in self._read_path_entries_from_section(
                        config_file, "configuration_files",
                    ):
                        (
                            local_expr,
                            backup_expr,
                        ) = self._resolve_platform_selectors_with_backup(path)
                        local_expr = self._expand_builtin_path_vars(local_expr)
                        backup_expr = self._expand_builtin_path_vars(
                            backup_expr, for_backup=True,
                        )
                        for local_path, backup_path in self._expand_brace_mappings(
                            local_expr, backup_expr,
                        ):
                            if any(
                                p.startswith("/")
                                for p in (local_path, backup_path)
                            ):
                                raise ValueError(
                                    "Unsupported absolute path in mapping: "
                                    f"{local_path!r} -> {backup_path!r}",
                                )
                            config_files.add(local_path)
                            config_mappings.add((local_path, backup_path))

    @staticmethod
    def get_config_files() -> set[str]:
        """
        Return the application configuration files.

        Return a list of configuration files describing the apps supported by
        Mackup. The files returned are absolute full path to those files.
        e.g. /usr/lib/mackup/applications/bash.cfg

        Only one config file per application should be returned, custom config
        having a priority over stock config. Legacy custom apps directory
        (~/.mackup/) takes priority over XDG location.

        Returns:
            set of strings.
        """
        # Configure the config parser
        apps_dir: str = os.path.join(
            os.path.dirname(os.path.realpath(__file__)), APPS_DIR,
        )

        # Legacy custom apps directory: ~/.mackup/
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
                if filename.endswith(".cfg"):
                    config_files.add(os.path.join(legacy_custom_apps_dir, filename))
                    custom_files.add(filename)

        # Get custom application config files from XDG directory
        # (only if not already in legacy directory)
        if os.path.isdir(xdg_custom_apps_dir):
            for filename in os.listdir(xdg_custom_apps_dir):
                if filename.endswith(".cfg") and filename not in custom_files:
                    config_files.add(os.path.join(xdg_custom_apps_dir, filename))
                    custom_files.add(filename)

        # Add the default provided app config files, but only if those are not
        # customized, as we don't want to overwrite custom app config.
        for filename in os.listdir(apps_dir):
            if filename.endswith(".cfg") and filename not in custom_files:
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
