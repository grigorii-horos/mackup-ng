"""
The applications database.

The Applications Database provides an easy to use interface to load application
data from the Mackup Database (files).
"""

import os
import platform
import re
import tomllib
from dataclasses import dataclass
from typing import ClassVar

from . import blocks, conditions, constants, dirs, utils
from .constants import APPS_DIR

# ${VAR} token; NAME is captured. Reserved names below are the dual-path
# built-ins; anything else is resolved from the environment / source_env.
_ENV_VAR_RE = re.compile(r"\$\{(\w+)\}")


@dataclass(frozen=True)
class Unit:
    """One step of a config's work: files to sync, then an action to run.

    Units are numbered by ``slot`` in final execution order — `pre` blocks,
    the top-level unit, `during` blocks, `post` blocks — so the sync loop is a
    plain walk in slot order with no phase logic left at execution time.
    """

    slot: int
    when: dict
    passed: bool
    mappings: tuple[tuple[str, str], ...]
    block: dict | None


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

        inner = path[start + 1 : end]
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
        suffix = path[end + 1 :]

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
        cls,
        path: str,
        *,
        for_backup: bool = False,
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

        inner = path[start + 1 : end]
        items = cls._split_top_level_items(inner)
        if len(items) <= 1:
            return {path}

        prefix = path[:start]
        suffix = path[end + 1 :]
        expanded: set[str] = set()
        for item in items:
            for candidate in cls._expand_braces(f"{prefix}{item}{suffix}"):
                expanded.add(candidate)
        return expanded

    @classmethod
    def _expand_brace_mappings(
        cls,
        local_expr: str,
        backup_expr: str,
    ) -> list[tuple[str, str]]:
        """
        Expand braces for local/backup expressions while preserving mapping intent.
        """
        local_expanded = sorted(cls._expand_braces(local_expr))
        backup_expanded = sorted(cls._expand_braces(backup_expr))

        if len(local_expanded) == 1 and len(backup_expanded) == 1:
            return [(local_expanded[0], backup_expanded[0])]
        if len(local_expanded) == len(backup_expanded):
            return list(zip(local_expanded, backup_expanded, strict=True))
        if len(backup_expanded) == 1:
            return [(local, backup_expanded[0]) for local in local_expanded]
        if len(local_expanded) == 1:
            return [(local_expanded[0], backup) for backup in backup_expanded]

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
                return str(match.group(0))
            value = os.environ.get(name)
            if value is None:
                value = cls._lookup_source_env(name, env_files)
            if value is None:
                raise KeyError(name)
            return value

        return _ENV_VAR_RE.sub(repl, path)

    @classmethod
    def _entry_to_exprs(
        cls,
        entry: str,
        env_files: list[str] | None = None,
    ) -> tuple[str, str]:
        """Resolve a configuration_files entry into (local_expr, backup_expr).

        Local == backup layout; selectors, built-in vars and env vars all apply.
        """
        env_files = env_files or []
        local_expr, backup_expr = cls._resolve_platform_selectors_with_backup(entry)
        local_expr = cls._expand_env_vars(
            cls._expand_builtin_path_vars(local_expr),
            env_files,
        )
        backup_expr = cls._expand_env_vars(
            cls._expand_builtin_path_vars(backup_expr, for_backup=True),
            env_files,
        )
        return local_expr, backup_expr

    @classmethod
    def _pair_to_exprs(
        cls,
        src: str,
        dest: str,
        env_files: list[str] | None = None,
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
        files: list[str],
        mappings: list[tuple[str, str]],
    ) -> None:
        """Brace-expand, reject absolute paths, and append local/backup pairs."""
        for local_path, backup_path in cls._expand_brace_mappings(
            local_expr,
            backup_expr,
        ):
            if any(p.startswith("/") for p in (local_path, backup_path)):
                raise ValueError(
                    "Unsupported absolute path in mapping: "
                    f"{local_path!r} -> {backup_path!r}",
                )
            if (local_path, backup_path) in mappings:
                continue
            if local_path not in files:
                files.append(local_path)
            mappings.append((local_path, backup_path))

    def __init__(self) -> None:
        """Create a ApplicationsDatabase instance."""
        # Build the dict that will contain the properties of each application
        self.apps: dict[str, dict[str, str | list[str]]] = {}
        self.app_file_mappings: dict[str, list[tuple[str, str, int]]] = {}
        self.app_blocks: dict[str, list[dict]] = {}
        self.app_units: dict[str, list[Unit]] = {}
        self.app_conditions: dict[str, dict] = {}
        self.app_env_files: dict[str, list[str]] = {}
        self.app_ignores: dict[str, list[str]] = {}
        self.app_order: list[str] = []

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
            self.app_order.append(app_name)
            when = data.get("when")
            if when is not None and not isinstance(when, dict):
                print(
                    utils.colorize_message(
                        f"Warning: {app_name}: top-level [when] must be a table, "
                        "ignoring",
                    ),
                )
            self.app_conditions[app_name] = dict(when) if isinstance(when, dict) else {}
            if isinstance(when, dict):
                bad_keys = conditions.unrecognized_keys(when)
                if bad_keys:
                    names = ", ".join(sorted(bad_keys))
                    print(
                        utils.colorize_message(
                            f"Warning: {app_name}: unrecognized [when] key(s): {names}",
                        ),
                    )

            # Fancy display name (falls back to the id)
            self.apps[app_name]["name"] = data.get(
                "name",
                legacy.get("name", app_name),
            )

            # The whole top level is one unit: top-level keys that are not
            # sync/meta form its action, if they carry an action sub-table.
            reserved = {
                "name",
                "files",
                "configuration_files",
                "mapped_files",
                "source_env",
                "when",
                "block",
                "ignore",
                "application",
            }
            top_block = {k: v for k, v in data.items() if k not in reserved}
            top_action = top_block if blocks.block_action(top_block) else None

            def _phase_of(block: dict, app: str = app_name) -> str:
                phase = block.get("phase", "during")
                if phase not in ("pre", "during", "post"):
                    print(
                        utils.colorize_message(
                            f"Warning: {app}: unknown phase {phase!r},"
                            ' treating it as "during"',
                        ),
                    )
                    return "during"
                return str(phase)

            raw_blocks = [b for b in data.get("block", []) if isinstance(b, dict)]
            ordered: list[dict | None] = [
                *[b for b in raw_blocks if _phase_of(b) == "pre"],
                None,  # placeholder for the top-level unit
                *[b for b in raw_blocks if _phase_of(b) == "during"],
                *[b for b in raw_blocks if _phase_of(b) == "post"],
            ]

            # Names ignored inside this config's paths, on top of the global
            # ignore files.
            ignored = data.get("ignore", legacy.get("ignore", []))
            if not isinstance(ignored, list):
                print(
                    utils.colorize_message(
                        f"Warning: {app_name}: ignore must be a list, ignoring",
                    ),
                )
                ignored = []
            self.app_ignores[app_name] = [str(pattern) for pattern in ignored]

            # Extra ${VAR} beyond the built-ins resolve from these files (+ env).
            env_files = list(
                data.get("source_env", legacy.get("source_env", [])),
            )
            self.app_env_files[app_name] = env_files

            # Add the configuration files to sync
            config_files: list[str] = []
            config_mappings: list[tuple[str, str, int]] = []
            self.apps[app_name]["configuration_files"] = config_files
            self.app_file_mappings[app_name] = config_mappings

            top_paths: list[str] = next(
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

            units: list[Unit] = []
            for slot, entry in enumerate(ordered):
                is_top = entry is None
                block = top_action if is_top else entry
                when = {} if entry is None else dict(entry.get("when", {}))
                if when:
                    bad_keys = conditions.unrecognized_keys(when)
                    if bad_keys:
                        names = ", ".join(sorted(bad_keys))
                        print(
                            utils.colorize_message(
                                f"Warning: {app_name}: unrecognized [when]"
                                f" key(s) in block: {names}",
                            ),
                        )
                passed = conditions.block_passes({"when": when})

                unit_files: list[str] = []
                unit_mappings: list[tuple[str, str]] = []
                raw_paths = top_paths if entry is None else entry.get("files", [])
                if not isinstance(raw_paths, list):
                    print(
                        utils.colorize_message(
                            f"Warning: {app_name}: block files must be a list,"
                            " ignoring them",
                        ),
                    )
                    raw_paths = []
                for path in raw_paths:
                    try:
                        local_expr, backup_expr = self._entry_to_exprs(
                            str(path),
                            env_files,
                        )
                    except KeyError as exc:
                        print(
                            utils.colorize_message(
                                f"Warning: {app_name}: unresolved var {exc} in"
                                f" {path!r}, skipping",
                            ),
                        )
                        continue
                    self._register_exprs(
                        local_expr,
                        backup_expr,
                        unit_files,
                        unit_mappings,
                    )
                if is_top:
                    for src, dest in data.get("mapped_files", {}).items():
                        try:
                            local_expr, backup_expr = self._pair_to_exprs(
                                str(src),
                                str(dest),
                                env_files,
                            )
                        except KeyError as exc:
                            print(
                                utils.colorize_message(
                                    f"Warning: {app_name}: unresolved var {exc}"
                                    f" in {src!r}, skipping",
                                ),
                            )
                            continue
                        self._register_exprs(
                            local_expr,
                            backup_expr,
                            unit_files,
                            unit_mappings,
                        )

                units.append(
                    Unit(
                        slot=slot,
                        when=when,
                        passed=passed,
                        mappings=tuple(unit_mappings),
                        block=block,
                    ),
                )
                if passed:
                    config_files.extend(unit_files)
                    config_mappings.extend(
                        (local, backup, slot) for local, backup in unit_mappings
                    )

            self.app_units[app_name] = units
            self.app_blocks[app_name] = [
                u.block for u in units if u.passed and u.block is not None
            ]

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

    def get_files(self, name: str) -> list[str]:
        """Return the local config paths of an application, in read order."""
        value = self.apps[name]["configuration_files"]
        assert isinstance(value, list)
        return list(value)

    def get_units(self, name: str) -> list[Unit]:
        """Return the config's units in slot order, passing or not."""
        return list(self.app_units.get(name, []))

    def get_file_mappings(self, name: str) -> list[tuple[str, str, int]]:
        """Return (local, backup, slot) triples of an application, in read order.

        Only units whose conditions hold contribute mappings.
        """
        return list(self.app_file_mappings[name])

    def get_app_order(self) -> list[str]:
        """Return app ids in config read order (weakest first)."""
        return list(self.app_order)

    def get_blocks(self, name: str) -> list[dict]:
        """Return the action blocks of the config's passing units, in slot order."""
        return list(self.app_blocks.get(name, []))

    def get_ignore_patterns(self, name: str) -> list[str]:
        """Patterns this config ignores inside its own paths."""
        return list(self.app_ignores.get(name, []))

    def get_env_files(self, name: str) -> list[str]:
        """Return the config's source_env files (for ${VAR} in blocks)."""
        return list(self.app_env_files.get(name, []))

    def get_conditions(self, name: str) -> dict:
        """Return the config's top-level ``[when]`` table (empty when absent)."""
        return dict(self.app_conditions.get(name, {}))

    def config_enabled(self, name: str) -> bool:
        """True when the config's conditions hold on this machine.

        A config that is not enabled declares nothing: no sync pairs and no
        blocks.
        """
        return conditions.config_passes({"when": self.app_conditions.get(name, {})})

    def get_failing_conditions(self, name: str) -> dict:
        """Return only the conditions in the config's ``[when]`` that do not hold."""
        return conditions.failing({"when": self.app_conditions.get(name, {})})

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
