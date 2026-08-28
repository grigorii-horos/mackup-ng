# Destination-Keyed Mapping Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the unordered set of `(local, backup)` pairs with a destination-keyed ordered model where one backup source may feed N local destinations, a repeated destination evicts the earlier pair, and precedence follows a documented read order.

**Architecture:** A new pure module `mapping.py` owns the model (`Pair`, last-wins resolution, grouping by source). `appsdb.py` stops using sets and hands out entries in read order. `application.py` gains group-based sync (all members of a fanout group are peers; newest mtime wins; directories merge per entry across N members). `main.py` builds one global plan for all selected apps, then executes it group by group.

**Tech Stack:** Python 3.12+, stdlib only (`tomllib`, `os`, `shutil`), `docopt-ng` for the CLI, `pytest` running `unittest.TestCase` classes.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-10-mapping-model-design.md`. Read it before Task 1.
- No TOML syntax change. `files` and `[mapped_files]` keep their current meaning.
- Python floor is `requires-python >=3.12`; `from __future__ import annotations` at the top of new modules, matching `blocks.py`.
- Tests are `unittest.TestCase` classes run by pytest, using `tempfile.mkdtemp()` and `HOME`/`XDG_CONFIG_HOME` overrides restored in `tearDown` — copy the harness in `tests/test_cli.py:14-91`.
- Run the full gate before each commit: `make check` (ruff + mypy + ty + pytest). At minimum run `uv run pytest` plus `uv run ruff check src tests`.
- Commit messages follow Conventional Commits (`feat:`, `fix:`, `refactor:`, `test:`, `docs:`), imperative mood.
- Read order, fixed everywhere: stock `mackup_ng/applications/*.toml` (alphabetical) → `$XDG_CONFIG_HOME/mackup/applications/*.toml` (alphabetical) → `~/.mackup/applications/*.toml` (alphabetical). A same-named custom file still excludes the file below it entirely.
- Within one config file: `files` entries in declaration order, then `[mapped_files]` entries in declaration order; brace expansion inside one entry expands alphabetically.

---

## File Structure

**Create:**

- `src/mackup_ng/mapping.py` — the model: `Pair`, `Eviction`, `build_pairs`, `group_by_source`, `group_owners`. Pure data, zero filesystem access, so it is testable without temp dirs.
- `tests/test_mapping_model.py` — unit tests for the above.
- `tests/test_appsdb_order.py` — read-order and ordered-getter tests.
- `tests/test_sync_groups.py` — fanout file and directory sync tests.
- `tests/test_rm_destinations.py` — destination-scoped removal tests.

**Modify:**

- `src/mackup_ng/appsdb.py` — `get_config_files()` returns an ordered list; per-app entries become ordered lists; new `get_app_order()`.
- `src/mackup_ng/application.py` — add `sync_group()`, `sync_members_directory()`, tombstone helpers keyed by destination, `remove_destination()`.
- `src/mackup_ng/main.py` — build the global plan once in `sync`, execute groups, report evictions/orphans under `-v`, rewrite `rm`, extend `show`.
- `AGENTS.md`, `README.md`, `doc/ARCHITECTURE.md` — document fanout, override and read order.

Note: `tests/test_mapping.py` already exists and covers `[mapped_files]` path templating. Leave it alone; new model tests go in `tests/test_mapping_model.py`.

---

### Task 1: The pure mapping model

**Files:**
- Create: `src/mackup_ng/mapping.py`
- Test: `tests/test_mapping_model.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `Pair(source: str, dest: str, owner_app: str)` — frozen dataclass.
  - `Eviction(evicted: Pair, winner: Pair)` — frozen dataclass.
  - `build_pairs(entries: Iterable[Pair]) -> tuple[list[Pair], list[Eviction]]`
  - `group_by_source(pairs: Sequence[Pair], evictions: Sequence[Eviction] = ()) -> tuple[dict[str, list[str]], list[str]]`
  - `group_owners(pairs: Sequence[Pair]) -> dict[str, str]`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_mapping_model.py`:

```python
"""Tests for the pure destination-keyed mapping model."""

import unittest

from mackup_ng.mapping import (
    Pair,
    build_pairs,
    group_by_source,
    group_owners,
)


class TestBuildPairs(unittest.TestCase):
    def test_keeps_order_and_allows_shared_source(self):
        entries = [
            Pair(".config/profile/user.js", ".config/work/user.js", "app-a"),
            Pair(".config/profile/user.js", ".config/home/user.js", "app-a"),
        ]
        pairs, evictions = build_pairs(entries)
        assert pairs == entries
        assert evictions == []

    def test_repeated_destination_evicts_previous_pair(self):
        first = Pair(".config/x", ".config/x", "app-a")
        second = Pair(".config/x-work", ".config/x", "zz-work")
        pairs, evictions = build_pairs([first, second])
        assert pairs == [second]
        assert len(evictions) == 1
        assert evictions[0].evicted == first
        assert evictions[0].winner == second

    def test_winner_moves_to_the_end_of_the_list(self):
        first = Pair(".config/a", ".config/a", "app-a")
        second = Pair(".config/b", ".config/b", "app-b")
        override = Pair(".config/a-alt", ".config/a", "zz-work")
        pairs, _ = build_pairs([first, second, override])
        assert pairs == [second, override]


class TestGroupBySource(unittest.TestCase):
    def test_groups_destinations_under_their_source(self):
        pairs = [
            Pair(".config/profile/user.js", ".config/work/user.js", "app-a"),
            Pair(".config/profile/user.js", ".config/home/user.js", "app-a"),
            Pair(".vimrc", ".vimrc", "vim"),
        ]
        groups, orphans = group_by_source(pairs)
        assert groups == {
            ".config/profile/user.js": [
                ".config/work/user.js",
                ".config/home/user.js",
            ],
            ".vimrc": [".vimrc"],
        }
        assert orphans == []

    def test_source_with_no_surviving_destination_is_orphaned(self):
        first = Pair(".config/x", ".config/x", "app-a")
        second = Pair(".config/x-work", ".config/x", "zz-work")
        pairs, evictions = build_pairs([first, second])
        _, orphans = group_by_source(pairs, evictions)
        assert orphans == [".config/x"]

    def test_evicted_source_still_used_elsewhere_is_not_orphaned(self):
        entries = [
            Pair(".config/x", ".config/x", "app-a"),
            Pair(".config/x", ".config/x-copy", "app-a"),
            Pair(".config/x-work", ".config/x", "zz-work"),
        ]
        pairs, evictions = build_pairs(entries)
        _, orphans = group_by_source(pairs, evictions)
        assert orphans == []


class TestGroupOwners(unittest.TestCase):
    def test_owner_is_the_first_pair_of_the_group(self):
        pairs = [
            Pair(".config/p", ".config/work", "app-a"),
            Pair(".config/p", ".config/home", "zz-work"),
        ]
        assert group_owners(pairs) == {".config/p": "app-a"}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_mapping_model.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mackup_ng.mapping'`

- [ ] **Step 3: Write the implementation**

Create `src/mackup_ng/mapping.py`:

```python
"""Destination-keyed mapping model.

A managed item is a :class:`Pair` — a backup ``source`` feeding a local
``dest``. Destinations are unique: a later pair for the same destination
evicts the earlier one. Sources may repeat, and a repeated source is what
expresses fanout (one backup file feeding several local files).

Everything here is pure: no filesystem access, no configuration parsing.
The caller resolves paths first and executes the resulting plan.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence


@dataclass(frozen=True)
class Pair:
    """One managed mapping: backup ``source`` feeds local ``dest``."""

    source: str
    dest: str
    owner_app: str


@dataclass(frozen=True)
class Eviction:
    """A pair displaced because a later pair claimed the same destination."""

    evicted: Pair
    winner: Pair


def build_pairs(entries: Iterable[Pair]) -> tuple[list[Pair], list[Eviction]]:
    """Resolve entries in read order; later pairs win their destination.

    Returns the surviving pairs (in final order, winners last) and the list
    of evictions, in the order they happened.
    """
    by_dest: dict[str, Pair] = {}
    evictions: list[Eviction] = []
    for pair in entries:
        previous = by_dest.pop(pair.dest, None)
        if previous is not None:
            evictions.append(Eviction(previous, pair))
        by_dest[pair.dest] = pair
    return list(by_dest.values()), evictions


def group_by_source(
    pairs: Sequence[Pair],
    evictions: Sequence[Eviction] = (),
) -> tuple[dict[str, list[str]], list[str]]:
    """Group destinations under their source; report orphaned sources.

    A source is orphaned when every pair that used it was evicted, so it
    feeds nothing on this machine.
    """
    groups: dict[str, list[str]] = {}
    for pair in pairs:
        groups.setdefault(pair.source, []).append(pair.dest)
    orphans = [
        eviction.evicted.source
        for eviction in evictions
        if eviction.evicted.source not in groups
    ]
    return groups, list(dict.fromkeys(orphans))


def group_owners(pairs: Sequence[Pair]) -> dict[str, str]:
    """Map each source to the app that first claimed it (for statistics)."""
    owners: dict[str, str] = {}
    for pair in pairs:
        owners.setdefault(pair.source, pair.owner_app)
    return owners
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_mapping_model.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check src/mackup_ng/mapping.py tests/test_mapping_model.py
uv run mypy src/mackup_ng/mapping.py
git add src/mackup_ng/mapping.py tests/test_mapping_model.py
git commit -m "feat: add destination-keyed mapping model"
```

---

### Task 2: Deterministic read order in appsdb

**Files:**
- Modify: `src/mackup_ng/appsdb.py:249-270` (`_expand_brace_mappings`), `:355-371` (`_register_exprs`), `:373-477` (`__init__`), `:479-537` (`get_config_files`), `:553-577` (getters)
- Test: `tests/test_appsdb_order.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces:
  - `ApplicationsDatabase.get_config_files() -> list[str]` (stock, then XDG custom, then `~/.mackup` custom; alphabetical within each tier)
  - `ApplicationsDatabase.get_app_order() -> list[str]` — app ids in the order their config file was read
  - `ApplicationsDatabase.get_file_mappings(name) -> list[tuple[str, str]]` — `(local, backup)` in declaration order
  - `ApplicationsDatabase.get_files(name) -> list[str]` — local paths in declaration order

- [ ] **Step 1: Write the failing tests**

Create `tests/test_appsdb_order.py`:

```python
"""Tests for deterministic config read order and ordered getters."""

import os
import shutil
import tempfile
import unittest

from mackup_ng.appsdb import ApplicationsDatabase


class TestReadOrder(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="mackup_order_home_")
        self._orig_home = os.environ.get("HOME")
        self._orig_xdg = os.environ.get("XDG_CONFIG_HOME")
        os.environ["HOME"] = self.home
        os.environ["XDG_CONFIG_HOME"] = os.path.join(self.home, ".config")
        self.legacy_dir = os.path.join(self.home, ".mackup", "applications")
        self.xdg_dir = os.path.join(
            self.home,
            ".config",
            "mackup",
            "applications",
        )
        os.makedirs(self.legacy_dir, exist_ok=True)
        os.makedirs(self.xdg_dir, exist_ok=True)

    def tearDown(self):
        for key, orig in (
            ("HOME", self._orig_home),
            ("XDG_CONFIG_HOME", self._orig_xdg),
        ):
            if orig is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = orig
        shutil.rmtree(self.home, ignore_errors=True)

    def _write(self, directory, name, body):
        with open(os.path.join(directory, f"{name}.toml"), "w") as handle:
            handle.write(f'name = "{name}"\n{body}')

    def test_custom_configs_are_read_after_stock_ones(self):
        self._write(self.xdg_dir, "zzz-xdg", 'files = [".xdgrc"]\n')
        self._write(self.legacy_dir, "aaa-legacy", 'files = [".legacyrc"]\n')
        order = ApplicationsDatabase().get_app_order()
        assert order.index("zzz-xdg") < order.index("aaa-legacy")
        assert order.index("bash") < order.index("zzz-xdg")

    def test_stock_configs_are_ordered_alphabetically(self):
        order = ApplicationsDatabase().get_app_order()
        stock = [name for name in order if name in {"bash", "git", "vim"}]
        assert stock == sorted(stock)

    def test_entries_keep_declaration_order(self):
        self._write(
            self.legacy_dir,
            "ordered",
            'files = [".zshrc", ".bashrc"]\n\n'
            "[mapped_files]\n"
            '".config/b" = ".config/a"\n',
        )
        db = ApplicationsDatabase()
        assert db.get_file_mappings("ordered") == [
            (".zshrc", ".zshrc"),
            (".bashrc", ".bashrc"),
            (".config/b", ".config/a"),
        ]
        assert db.get_files("ordered") == [".zshrc", ".bashrc", ".config/b"]

    def test_same_named_custom_config_replaces_the_stock_one(self):
        self._write(self.legacy_dir, "bash", 'files = [".only-this"]\n')
        db = ApplicationsDatabase()
        assert db.get_files("bash") == [".only-this"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_appsdb_order.py -v`
Expected: FAIL — `AttributeError: 'ApplicationsDatabase' object has no attribute 'get_app_order'`, and the ordering assertions fail because the getters return sets.

- [ ] **Step 3: Replace `get_config_files` with an ordered list**

In `src/mackup_ng/appsdb.py`, replace the body of `get_config_files` (keep the docstring, update its wording) with:

```python
@staticmethod
def get_config_files() -> list[str]:
    """
    Return the application configuration files in precedence order.

    Stock files come first (alphabetical), then the XDG custom directory,
    then ``~/.mackup/applications`` — later files win when two configs
    claim the same destination. A custom file still shadows a stock file
    with the same name entirely.

    Returns:
        list of absolute paths, weakest first.
    """
    apps_dir: str = os.path.join(
        os.path.dirname(os.path.realpath(__file__)),
        APPS_DIR,
    )
    legacy_custom_apps_dir: str = os.path.join(os.environ["HOME"], CUSTOM_APPS_DIR)
    xdg_config_home: str = os.environ.get(
        "XDG_CONFIG_HOME",
        os.path.join(os.environ["HOME"], ".config"),
    )
    xdg_custom_apps_dir: str = os.path.join(xdg_config_home, CUSTOM_APPS_DIR_XDG)

    def toml_names(directory: str) -> set[str]:
        if not os.path.isdir(directory):
            return set()
        return {name for name in os.listdir(directory) if name.endswith(".toml")}

    legacy_names = toml_names(legacy_custom_apps_dir)
    xdg_names = toml_names(xdg_custom_apps_dir) - legacy_names
    stock_names = toml_names(apps_dir) - legacy_names - xdg_names

    return [
        *(os.path.join(apps_dir, name) for name in sorted(stock_names)),
        *(os.path.join(xdg_custom_apps_dir, name) for name in sorted(xdg_names)),
        *(os.path.join(legacy_custom_apps_dir, name) for name in sorted(legacy_names)),
    ]
```

- [ ] **Step 4: Make the per-app entries ordered lists**

In `__init__`, change the container declarations and record the read order:

```python
        self.apps: dict[str, dict[str, str | list[str]]] = {}
        self.app_file_mappings: dict[str, list[tuple[str, str]]] = {}
        self.app_blocks: dict[str, list[dict]] = {}
        self.app_env_files: dict[str, list[str]] = {}
        self.app_order: list[str] = []
```

Right after `app_name` is computed and `self.apps[app_name] = {}` runs, append the id (a shadowed name can only appear once, so no duplicate check is needed):

```python
            self.app_order.append(app_name)
```

Change the two local containers from sets to lists:

```python
            config_files: list[str] = []
            config_mappings: list[tuple[str, str]] = []
```

- [ ] **Step 5: Make expansion and registration order-preserving**

`_expand_brace_mappings` currently returns a `set`; make it return a deterministic list. Change its signature to `-> list[tuple[str, str]]` and each `return` to a sorted list:

```python
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
```

`local_expanded` / `backup_expanded` are already `sorted(...)` lists, so the output is deterministic.

Then update `_register_exprs` to append while skipping exact duplicates (its parameter names change from `files_set` / `mappings_set` to `files` / `mappings`):

```python
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
```

- [ ] **Step 6: Update the getters**

```python
def get_files(self, name: str) -> list[str]:
    """Return the local config paths of an application, in read order."""
    value = self.apps[name]["configuration_files"]
    assert isinstance(value, list)
    return list(value)


def get_file_mappings(self, name: str) -> list[tuple[str, str]]:
    """Return (local, backup) pairs of an application, in read order."""
    return list(self.app_file_mappings[name])


def get_app_order(self) -> list[str]:
    """Return app ids in config read order (weakest first)."""
    return list(self.app_order)
```

- [ ] **Step 7: Run the tests**

Run: `uv run pytest tests/test_appsdb_order.py tests/test_mapping.py tests/test_appsdb_blocks.py tests/test_appsdb_xdg.py -v`
Expected: PASS. If `tests/test_mapping.py` compares against sets, convert those assertions to lists or wrap the call in `set(...)` — the pairs themselves are unchanged.

- [ ] **Step 8: Fix type errors in callers and commit**

`main.py` passes `app_db.get_file_mappings(...)` into `ApplicationProfile`, which asserts `isinstance(files, set)`. Relax that assertion now so the tree stays green:

In `src/mackup_ng/application.py:34-35`, replace `assert isinstance(files, set)` with:

```python
        assert isinstance(files, set | list)
        files = set(files)
```

and widen the parameter type to `set[str] | set[tuple[str, str]] | list[str] | list[tuple[str, str]]`.

```bash
uv run pytest
uv run ruff check src tests
uv run mypy src
git add -A src/mackup_ng/appsdb.py src/mackup_ng/application.py tests/test_appsdb_order.py
git commit -m "refactor: read app configs in deterministic precedence order"
```

---

### Task 3: Group sync for files

**Files:**
- Modify: `src/mackup_ng/application.py` (add methods; keep `sync_files` untouched for now)
- Test: `tests/test_sync_groups.py`

**Interfaces:**
- Consumes: nothing from Tasks 1–2 at runtime (the group is passed in).
- Produces:
  - `ApplicationProfile.member_paths(source: str, dests: list[str]) -> list[str]` — absolute paths, backup first.
  - `ApplicationProfile.sync_group(source: str, dests: list[str]) -> dict[str, int]` — stats dict with the existing keys `backed_up`, `restored`, `synchronized`, `deleted`, `skipped`, `errors`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_sync_groups.py`:

```python
"""Tests for fanout group synchronization."""

import os
import shutil
import tempfile
import unittest
from unittest.mock import Mock

from mackup_ng.application import ApplicationProfile
from mackup_ng.mackup import Mackup


class TestSyncGroupFiles(unittest.TestCase):
    def setUp(self):
        self.mackup = Mock(spec=Mackup)
        self.mackup.mackup_folder = tempfile.mkdtemp(prefix="mackup_group_backup_")
        self.home = tempfile.mkdtemp(prefix="mackup_group_home_")
        self._orig_home = os.environ.get("HOME")
        os.environ["HOME"] = self.home
        self.profile = ApplicationProfile(
            mackup=self.mackup,
            files=set(),
            dry_run=False,
            verbose=False,
        )

    def tearDown(self):
        if self._orig_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._orig_home
        shutil.rmtree(self.home, ignore_errors=True)
        shutil.rmtree(self.mackup.mackup_folder, ignore_errors=True)

    def _write(self, path, content, mtime):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as handle:
            handle.write(content)
        os.utime(path, (mtime, mtime))

    def test_backup_source_fans_out_to_every_destination(self):
        source = os.path.join(self.mackup.mackup_folder, ".config/app/user.js")
        self._write(source, "from-backup", 3000)
        stats = self.profile.sync_group(
            ".config/app/user.js",
            [".config/work/user.js", ".config/home/user.js"],
        )
        for dest in (".config/work/user.js", ".config/home/user.js"):
            with open(os.path.join(self.home, dest)) as handle:
                assert handle.read() == "from-backup"
        assert stats["restored"] == 2

    def test_newest_destination_wins_over_the_whole_group(self):
        source = os.path.join(self.mackup.mackup_folder, ".config/app/user.js")
        work = os.path.join(self.home, ".config/work/user.js")
        personal = os.path.join(self.home, ".config/home/user.js")
        self._write(source, "old-backup", 1000)
        self._write(work, "newest", 5000)
        self._write(personal, "stale", 2000)

        stats = self.profile.sync_group(
            ".config/app/user.js",
            [".config/work/user.js", ".config/home/user.js"],
        )

        for path in (source, personal):
            with open(path) as handle:
                assert handle.read() == "newest"
        assert stats["backed_up"] == 1
        assert stats["restored"] == 1

    def test_group_with_no_existing_member_is_a_no_op(self):
        stats = self.profile.sync_group(".config/missing", [".config/nowhere"])
        assert not any(stats.values())
        assert not os.path.exists(os.path.join(self.home, ".config/nowhere"))

    def test_dry_run_reports_without_writing(self):
        source = os.path.join(self.mackup.mackup_folder, ".config/app/user.js")
        self._write(source, "from-backup", 3000)
        profile = ApplicationProfile(
            mackup=self.mackup,
            files=set(),
            dry_run=True,
            verbose=False,
        )
        stats = profile.sync_group(".config/app/user.js", [".config/work/user.js"])
        assert stats["restored"] == 1
        assert not os.path.exists(os.path.join(self.home, ".config/work/user.js"))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_sync_groups.py -v`
Expected: FAIL with `AttributeError: 'ApplicationProfile' object has no attribute 'sync_group'`

- [ ] **Step 3: Implement group sync for files**

Add to `ApplicationProfile` in `src/mackup_ng/application.py`, after `get_filepaths`:

```python
def member_paths(self, source: str, dests: list[str]) -> list[str]:
    """Absolute paths of a fanout group: the backup source, then the destinations."""
    return [
        os.path.join(self.mackup.mackup_folder, source),
        *(os.path.join(os.environ["HOME"], dest) for dest in dests),
    ]


@staticmethod
def new_stats() -> dict[str, int]:
    """A zeroed statistics dict with every key the reporter expects."""
    return {
        "backed_up": 0,
        "restored": 0,
        "synchronized": 0,
        "deleted": 0,
        "skipped": 0,
        "errors": 0,
    }


def sync_group(self, source: str, dests: list[str]) -> dict[str, int]:
    """Sync one fanout group: newest member wins and reaches every other.

    Members are peers — the backup side has no special authority. A group
    of two members is the ordinary 1:1 case.
    """
    stats = self.new_stats()
    members = self.member_paths(source, dests)
    backup_path = members[0]
    existing = [path for path in members if os.path.isfile(path) or os.path.isdir(path)]
    if not existing:
        return stats

    if any(os.path.isdir(path) for path in existing):
        return self.sync_members_directory(members)

    winner = max(existing, key=self.get_effective_mtime)
    winner_mtime = self.get_effective_mtime(winner)

    for member in members:
        if member == winner:
            continue
        if os.path.exists(member):
            if os.path.samefile(member, winner):
                if self.verbose:
                    self._print(
                        f"Skipping {member}\n  already linked to\n  {winner}",
                    )
                stats["skipped"] += 1
                continue
            if self.get_effective_mtime(member) >= winner_mtime:
                if self.verbose:
                    self._print(
                        f"Skipping {member}\n  not older than\n  {winner}",
                    )
                stats["skipped"] += 1
                continue

        if self.verbose:
            self._print(f"Copying\n  {winner}\n  to\n  {member} ...")

        if not self.dry_run:
            try:
                if os.path.lexists(member):
                    utils.delete(member)
                utils.copy(winner, member)
            except PermissionError as e:
                self._print(
                    f"Error: Unable to copy file from {winner} to "
                    f"{member} due to permission issue: {e}",
                )
                stats["errors"] += 1
                continue

        if member == backup_path:
            stats["backed_up"] += 1
        else:
            stats["restored"] += 1

    return stats
```

Add a temporary stub so the file-only tests run before Task 4 implements it:

```python
    def sync_members_directory(self, members: list[str]) -> dict[str, int]:
        """Merge N directory members entry by entry (implemented in Task 4)."""
        raise NotImplementedError
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_sync_groups.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
uv run ruff check src/mackup_ng/application.py tests/test_sync_groups.py
git add src/mackup_ng/application.py tests/test_sync_groups.py
git commit -m "feat: sync fanout groups of files by newest member"
```

---

### Task 4: Directory union across N members

**Files:**
- Modify: `src/mackup_ng/application.py` (replace the `sync_members_directory` stub)
- Test: `tests/test_sync_groups.py` (add a second test class)

**Interfaces:**
- Consumes: `ApplicationProfile.member_paths`, `ApplicationProfile.new_stats`, `ApplicationProfile.get_effective_mtime`, `ApplicationProfile.collect_relative_entries`, `ApplicationProfile.ensure_directory`, `ApplicationProfile.copy_item` (all already on the class).
- Produces: `ApplicationProfile.sync_members_directory(members: list[str]) -> dict[str, int]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_sync_groups.py`:

```python
class TestSyncGroupDirectories(unittest.TestCase):
    def setUp(self):
        self.mackup = Mock(spec=Mackup)
        self.mackup.mackup_folder = tempfile.mkdtemp(prefix="mackup_gdir_backup_")
        self.home = tempfile.mkdtemp(prefix="mackup_gdir_home_")
        self._orig_home = os.environ.get("HOME")
        os.environ["HOME"] = self.home
        self.profile = ApplicationProfile(
            mackup=self.mackup,
            files=set(),
            dry_run=False,
            verbose=False,
        )

    def tearDown(self):
        if self._orig_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._orig_home
        shutil.rmtree(self.home, ignore_errors=True)
        shutil.rmtree(self.mackup.mackup_folder, ignore_errors=True)

    def _write(self, path, content, mtime):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as handle:
            handle.write(content)
        os.utime(path, (mtime, mtime))

    def test_entries_union_across_all_members(self):
        backup = os.path.join(self.mackup.mackup_folder, "profile")
        work = os.path.join(self.home, "work")
        personal = os.path.join(self.home, "personal")
        self._write(os.path.join(backup, "shared.txt"), "backup", 1000)
        self._write(os.path.join(work, "only-work.txt"), "work", 2000)
        self._write(os.path.join(personal, "shared.txt"), "newest", 5000)

        stats = self.profile.sync_group("profile", ["work", "personal"])

        for root in (backup, work, personal):
            with open(os.path.join(root, "shared.txt")) as handle:
                assert handle.read() == "newest"
            with open(os.path.join(root, "only-work.txt")) as handle:
                assert handle.read() == "work"
        assert stats["synchronized"] == 1

    def test_nested_entries_are_created_in_every_member(self):
        backup = os.path.join(self.mackup.mackup_folder, "profile")
        self._write(os.path.join(backup, "nested", "deep.txt"), "deep", 4000)

        self.profile.sync_group("profile", ["work", "personal"])

        for name in ("work", "personal"):
            nested = os.path.join(self.home, name, "nested", "deep.txt")
            with open(nested) as handle:
                assert handle.read() == "deep"

    def test_unchanged_group_reports_skipped(self):
        backup = os.path.join(self.mackup.mackup_folder, "profile")
        work = os.path.join(self.home, "work")
        self._write(os.path.join(backup, "same.txt"), "same", 4000)
        self._write(os.path.join(work, "same.txt"), "same", 4000)

        stats = self.profile.sync_group("profile", ["work"])

        assert stats["synchronized"] == 0
        assert stats["skipped"] == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_sync_groups.py::TestSyncGroupDirectories -v`
Expected: FAIL with `NotImplementedError`

- [ ] **Step 3: Implement the union merge**

Replace the stub in `src/mackup_ng/application.py`:

```python
def sync_members_directory(
    self,
    members: list[str],
    backup_path: str,
) -> dict[str, int]:
    """Merge N directory members entry by entry; newest entry wins.

    Every member ends up holding the union of the group's entries. A
    member that does not exist yet is created, so a backup directory can
    fan out to fresh destinations.
    """
    stats = self.new_stats()
    present = [path for path in members if os.path.isdir(path)]
    if not present:
        return stats

    root_source = max(present, key=os.path.getmtime)
    if not self.dry_run:
        for member in members:
            self.ensure_directory(member, root_source)

    entries: list[str] = []
    for member in present:
        for entry in sorted(self.collect_relative_entries(member)):
            if entry not in entries:
                entries.append(entry)

    changed = False
    for entry in entries:
        targets = [os.path.join(member, entry) for member in members]
        holders = [path for path in targets if os.path.exists(path)]
        if not holders:
            continue
        winner = max(holders, key=self.get_effective_mtime)
        winner_mtime = self.get_effective_mtime(winner)
        winner_is_dir = os.path.isdir(winner)

        for target in targets:
            if target == winner:
                continue
            if os.path.exists(target):
                if os.path.isdir(target) == winner_is_dir and (
                    self.get_effective_mtime(target) >= winner_mtime
                ):
                    continue
            if self.dry_run:
                changed = True
                continue
            try:
                if winner_is_dir:
                    if os.path.lexists(target) and not os.path.isdir(target):
                        utils.delete(target)
                    self.ensure_directory(target, winner)
                else:
                    if self.verbose:
                        self._print(f"Copying {entry} to {target}")
                    self.copy_item(winner, target)
            except PermissionError as e:
                self._print(
                    f"Error: Unable to copy {winner} to {target} "
                    f"due to permission issue: {e}",
                )
                stats["errors"] += 1
                continue
            changed = True

    if changed:
        stats["synchronized"] += 1
    else:
        stats["skipped"] += 1
    return stats
```

Note `ensure_directory(path, mode_from)` already does `os.makedirs(..., exist_ok=True)` plus mtime mirroring, so it both creates missing members and keeps root mtimes aligned.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_sync_groups.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
uv run pytest tests/test_sync_groups.py tests/test_application.py
uv run ruff check src/mackup_ng/application.py tests/test_sync_groups.py
git add src/mackup_ng/application.py tests/test_sync_groups.py
git commit -m "feat: merge fanout directory groups entry by entry"
```

---

### Task 5: Destination-keyed tombstones and the global sync plan

**Files:**
- Modify: `src/mackup_ng/application.py` (tombstone helpers), `src/mackup_ng/main.py:286-323` (the `sync` branch)
- Test: `tests/test_cli.py` (add tests to the existing `TestCLI` class)

**Interfaces:**
- Consumes: `mapping.Pair`, `mapping.build_pairs`, `mapping.group_by_source`, `mapping.group_owners` (Task 1); `ApplicationsDatabase.get_app_order`, `get_file_mappings` (Task 2); `ApplicationProfile.sync_group` (Tasks 3–4).
- Produces:
  - `ApplicationProfile.read_tombstones() -> set[str]` (alias kept: `read_deleted_files`)
  - `ApplicationProfile.apply_tombstones(groups: dict[str, list[str]], tombstoned: set[str]) -> dict[str, int]`
  - `main.build_sync_plan(app_db: ApplicationsDatabase, apps_to_sync: set[str]) -> tuple[list[mapping.Pair], list[mapping.Eviction]]`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cli.py` inside `TestCLI`:

```python
def _write_custom_app(self, app_id, body):
    path = os.path.join(self.custom_apps_dir, f"{app_id}.toml")
    with open(path, "w") as handle:
        handle.write(f'name = "{app_id}"\n{body}')
    with open(self.config_path, "a") as handle:
        handle.write(f"{app_id}\n")


def test_sync_fans_backup_out_to_two_destinations(self):
    self._write_custom_app(
        "fanout",
        '[mapped_files]\n".work.rc" = ".shared.rc"\n".home.rc" = ".shared.rc"\n',
    )
    source = os.path.join(self.mackup_folder, ".shared.rc")
    os.makedirs(self.mackup_folder, exist_ok=True)
    with open(source, "w") as handle:
        handle.write("shared=1\n")

    with patch("sys.argv", ["mackup", "sync"]):
        main()

    for name in (".work.rc", ".home.rc"):
        with open(os.path.join(self.test_home, name)) as handle:
            assert handle.read() == "shared=1\n"


def test_later_config_overrides_the_destination(self):
    self._write_custom_app("aaa-base", 'files = [".overridden"]\n')
    self._write_custom_app(
        "zzz-override",
        '[mapped_files]\n".overridden" = ".from-work"\n',
    )
    os.makedirs(self.mackup_folder, exist_ok=True)
    with open(os.path.join(self.mackup_folder, ".from-work"), "w") as handle:
        handle.write("work\n")
    with open(os.path.join(self.mackup_folder, ".overridden"), "w") as handle:
        handle.write("base\n")

    with patch("sys.argv", ["mackup", "sync"]):
        main()

    with open(os.path.join(self.test_home, ".overridden")) as handle:
        assert handle.read() == "work\n"
    # the evicted source keeps its content and is left alone
    with open(os.path.join(self.mackup_folder, ".overridden")) as handle:
        assert handle.read() == "base\n"


def test_tombstoned_destination_stays_removed_but_group_survives(self):
    self._write_custom_app(
        "fanout",
        '[mapped_files]\n".work.rc" = ".shared.rc"\n".home.rc" = ".shared.rc"\n',
    )
    os.makedirs(self.mackup_folder, exist_ok=True)
    with open(os.path.join(self.mackup_folder, ".shared.rc"), "w") as handle:
        handle.write("shared=1\n")
    with open(
        os.path.join(self.mackup_folder, ".mackup-deletions"),
        "w",
    ) as handle:
        handle.write(".work.rc\n")

    with patch("sys.argv", ["mackup", "sync"]):
        main()

    assert not os.path.exists(os.path.join(self.test_home, ".work.rc"))
    assert os.path.exists(os.path.join(self.test_home, ".home.rc"))
    assert os.path.exists(os.path.join(self.mackup_folder, ".shared.rc"))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k "fans_backup or overrides or tombstoned" -v`
Expected: FAIL — the fanout test finds only one destination written (whichever pair the old per-app loop reached), and the override test finds `.overridden` restored from `.overridden`.

- [ ] **Step 3: Add destination-keyed tombstone helpers**

In `src/mackup_ng/application.py`, add next to the existing deletion helpers (keep `read_deleted_files` / `write_deleted_files` / `record_deleted_file` as they are — `rm` still uses them):

```python
def read_tombstones(self) -> set[str]:
    """Normalized destinations recorded as explicitly removed."""
    return self.read_deleted_files()


def apply_tombstones(
    self,
    groups: dict[str, list[str]],
    tombstoned: set[str],
) -> dict[str, int]:
    """Delete tombstoned destinations, and sources left with no destination.

    ``groups`` is the unfiltered plan: it still contains the tombstoned
    destinations, so a source can tell whether any live destination
    remains before it is deleted.
    """
    stats = self.new_stats()
    for source, dests in groups.items():
        dead = [
            dest for dest in dests if self.normalize_relative_path(dest) in tombstoned
        ]
        if not dead:
            continue
        victims = [os.path.join(os.environ["HOME"], dest) for dest in dead]
        if len(dead) == len(dests):
            victims.append(os.path.join(self.mackup.mackup_folder, source))
        for filepath in victims:
            if not os.path.lexists(filepath):
                continue
            if self.verbose:
                self._print(f"Deleting\n  {filepath} ...")
            if self.dry_run:
                stats["deleted"] += 1
                continue
            try:
                utils.delete(filepath)
                stats["deleted"] += 1
            except PermissionError as e:
                self._print(
                    f"Error: Unable to delete file {filepath} "
                    f"due to permission issue: {e}",
                )
                stats["errors"] += 1
    return stats
```

- [ ] **Step 4: Build the plan once in `main.py`**

Add the import and a plan builder above `main()` in `src/mackup_ng/main.py`:

```python
from . import blocks, dconf, hooks, mapping, utils
```

```python
def build_sync_plan(
    app_db: ApplicationsDatabase,
    apps_to_sync: set[str],
) -> tuple[list[mapping.Pair], list[mapping.Eviction]]:
    """Collect every selected app's pairs in read order and resolve them."""
    entries = [
        mapping.Pair(source=backup, dest=local, owner_app=app_name)
        for app_name in app_db.get_app_order()
        if app_name in apps_to_sync and app_db.app_has_sync(app_name)
        for local, backup in app_db.get_file_mappings(app_name)
    ]
    return mapping.build_pairs(entries)
```

Then rewrite the `sync` branch body between `to_backup = mckp.get_apps_to_backup()` and the dconf restore call:

```python
to_backup = mckp.get_apps_to_backup()
pairs, evictions = build_sync_plan(app_db, to_backup)
all_groups, orphans = mapping.group_by_source(pairs, evictions)
owners = mapping.group_owners(pairs)

planner = ApplicationProfile(mckp, set(), dry_run, verbose)
tombstoned = planner.read_tombstones()
deletion_stats = planner.apply_tombstones(all_groups, tombstoned)

live_pairs = [
    pair
    for pair in pairs
    if ApplicationProfile.normalize_relative_path(pair.dest) not in tombstoned
]
groups, _ = mapping.group_by_source(live_pairs)
groups_by_owner: dict[str, list[tuple[str, list[str]]]] = {}
for source, dests in groups.items():
    groups_by_owner.setdefault(owners[source], []).append((source, dests))

for app_name in sorted(app_db.get_app_names()):
    env_files = app_db.get_env_files(app_name)
    cfg_blocks = app_db.get_blocks(app_name)
    pretty_name = app_db.get_name(app_name)

    tally = blocks.apply_blocks(cfg_blocks, "pre", env_files, dry_run)

    stats: dict[str, int] | None = None
    owned = groups_by_owner.get(app_name)
    if owned:
        app = ApplicationProfile(mckp, set(), dry_run, verbose)
        print_app_header(app_name, pretty_name)
        stats = ApplicationProfile.new_stats()
        for source, dests in owned:
            for key, value in app.sync_group(source, dests).items():
                stats[key] += value

    tally += blocks.apply_blocks(cfg_blocks, "post", env_files, dry_run)
    report_config(pretty_name, stats, tally)

if deletion_stats["deleted"]:
    print(
        utils.colorize_message(
            f"Deleted {deletion_stats['deleted']} tombstoned path(s)",
        ),
    )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -v`
Expected: PASS, including the pre-existing sync and tombstone tests.

- [ ] **Step 6: Commit**

```bash
uv run pytest
uv run ruff check src tests
uv run mypy src
git add src/mackup_ng/application.py src/mackup_ng/main.py tests/test_cli.py
git commit -m "feat: resolve one global sync plan with fanout groups"
```

---

### Task 6: Verbose diagnostics and `show`

**Files:**
- Modify: `src/mackup_ng/main.py` (the `sync` branch after the plan is built; the `show` branch at `:256-283`)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `mapping.Eviction`, the `orphans` list and `groups` dict from Task 5.
- Produces: no new API — output only.

- [ ] **Step 1: Write the failing tests**

Add to `TestCLI` in `tests/test_cli.py`:

```python
def test_verbose_sync_reports_evictions_and_orphans(self):
    self._write_custom_app("aaa-base", 'files = [".overridden"]\n')
    self._write_custom_app(
        "zzz-override",
        '[mapped_files]\n".overridden" = ".from-work"\n',
    )
    os.makedirs(self.mackup_folder, exist_ok=True)
    with open(os.path.join(self.mackup_folder, ".from-work"), "w") as handle:
        handle.write("work\n")

    buffer = io.StringIO()
    with (
        patch("sys.stdout", buffer),
        patch(
            "sys.argv",
            ["mackup", "-v", "sync"],
        ),
    ):
        main()
    output = buffer.getvalue()
    assert "evicted by zz" in output or "evicted by" in output
    assert ".overridden" in output
    assert "no destination" in output


def test_show_reports_fanout_destinations(self):
    self._write_custom_app(
        "fanout",
        '[mapped_files]\n".work.rc" = ".shared.rc"\n".home.rc" = ".shared.rc"\n',
    )
    buffer = io.StringIO()
    with (
        patch("sys.stdout", buffer),
        patch(
            "sys.argv",
            ["mackup", "show", "fanout"],
        ),
    ):
        main()
    output = buffer.getvalue()
    assert ".work.rc <- .shared.rc" in output
    assert "fanout: 2 destinations" in output
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k "verbose_sync_reports or show_reports_fanout" -v`
Expected: FAIL with `AssertionError` — the strings are not printed yet.

- [ ] **Step 3: Print evictions and orphans under `-v`**

In the `sync` branch, right after `owners = mapping.group_owners(pairs)`:

```python
        if verbose:
            for eviction in evictions:
                print(
                    utils.colorize_message(
                        f"{eviction.evicted.dest} <- {eviction.evicted.source} "
                        f"({eviction.evicted.owner_app}) evicted by "
                        f"{eviction.winner.owner_app}",
                    ),
                )
            for orphan in orphans:
                print(
                    utils.colorize_message(
                        f"{orphan} has no destination, left untouched",
                    ),
                )
```

- [ ] **Step 4: Show the resolved pairs of one application**

Replace the `Configuration files:` part of the `show` branch with a pair listing that marks fanout and lost entries:

```python
mappings = app_db.get_file_mappings(requested_app_name)
if mappings:
    pairs, _ = build_sync_plan(app_db, set(app_db.get_app_names()))
    winners = {pair.dest: pair for pair in pairs}
    fanout = Counter(pair.source for pair in pairs)
    print(bold("Configuration files:"))
    for local, backup in mappings:
        winner = winners.get(local)
        if winner is None or winner.source != backup:
            lost = utils.style_text(
                f"(overridden by {winner.owner_app})" if winner else "(overridden)",
                color=utils.AnsiColor.GRAY,
            )
            print(f"{dash} {local} <- {backup} {lost}")
            continue
        extra = ""
        if fanout[backup] > 1:
            extra = " " + utils.style_text(
                f"(fanout: {fanout[backup]} destinations)",
                color=utils.AnsiColor.GRAY,
            )
        print(f"{dash} {local} <- {backup}{extra}")
```

Add the import at the top of `main.py`:

```python
from collections import Counter
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
uv run ruff check src tests
git add src/mackup_ng/main.py tests/test_cli.py
git commit -m "feat: report evictions, orphans and fanout in sync -v and show"
```

---

### Task 7: `rm` removes one destination

**Files:**
- Modify: `src/mackup_ng/main.py:372-435` (the `rm` branch), `src/mackup_ng/application.py` (add `remove_destination`)
- Test: `tests/test_rm_destinations.py`

**Interfaces:**
- Consumes: `build_sync_plan` (Task 5), `mapping.group_by_source` (Task 1).
- Produces: `ApplicationProfile.remove_destination(source: str, dest: str, siblings: int) -> dict[str, int]` — deletes the local destination, records its tombstone, and deletes the backup source only when `siblings == 0`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_rm_destinations.py`:

```python
"""Removal is scoped to one destination, not the whole fanout group."""

import io
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from mackup_ng import utils
from mackup_ng.main import main


class TestRemoveDestination(unittest.TestCase):
    def setUp(self):
        self.test_home = tempfile.mkdtemp(prefix="mackup_rmdest_home_")
        self.test_storage = tempfile.mkdtemp(prefix="mackup_rmdest_storage_")
        self.mackup_folder = os.path.join(self.test_storage, "Mackup")
        os.makedirs(self.mackup_folder, exist_ok=True)
        self._orig_home = os.environ.get("HOME")
        self._orig_xdg = os.environ.get("XDG_CONFIG_HOME")
        os.environ["HOME"] = self.test_home
        os.environ["XDG_CONFIG_HOME"] = os.path.join(self.test_home, ".config")

        with open(os.path.join(self.test_home, ".mackup.cfg"), "w") as handle:
            handle.write(
                "[storage]\nengine = file_system\n"
                f"path = {self.test_storage}\ndirectory = Mackup\n\n"
                "[applications_to_sync]\nfanout\n",
            )
        apps_dir = os.path.join(self.test_home, ".mackup", "applications")
        os.makedirs(apps_dir, exist_ok=True)
        with open(os.path.join(apps_dir, "fanout.toml"), "w") as handle:
            handle.write(
                'name = "fanout"\n\n[mapped_files]\n'
                '".work.rc" = ".shared.rc"\n'
                '".home.rc" = ".shared.rc"\n',
            )
        with open(os.path.join(self.mackup_folder, ".shared.rc"), "w") as handle:
            handle.write("shared=1\n")
        utils.FORCE_YES = True

    def tearDown(self):
        for key, orig in (
            ("HOME", self._orig_home),
            ("XDG_CONFIG_HOME", self._orig_xdg),
        ):
            if orig is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = orig
        shutil.rmtree(self.test_home, ignore_errors=True)
        shutil.rmtree(self.test_storage, ignore_errors=True)
        utils.FORCE_YES = False

    def test_rm_one_destination_keeps_sibling_and_source(self):
        with patch("sys.argv", ["mackup", "sync"]):
            main()
        buffer = io.StringIO()
        with (
            patch("sys.stdout", buffer),
            patch(
                "sys.argv",
                ["mackup", "rm", ".work.rc"],
            ),
        ):
            main()

        assert not os.path.exists(os.path.join(self.test_home, ".work.rc"))
        assert os.path.exists(os.path.join(self.test_home, ".home.rc"))
        assert os.path.exists(os.path.join(self.mackup_folder, ".shared.rc"))
        assert "still feeds 1 destination" in buffer.getvalue()

    def test_rm_last_destination_removes_the_source(self):
        with patch("sys.argv", ["mackup", "sync"]):
            main()
        for name in (".work.rc", ".home.rc"):
            with patch("sys.argv", ["mackup", "rm", name]):
                main()

        assert not os.path.exists(os.path.join(self.mackup_folder, ".shared.rc"))

    def test_tombstone_records_the_destination(self):
        with patch("sys.argv", ["mackup", "sync"]):
            main()
        with patch("sys.argv", ["mackup", "rm", ".work.rc"]):
            main()

        with open(os.path.join(self.mackup_folder, ".mackup-deletions")) as handle:
            assert handle.read().split() == [".work.rc"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_rm_destinations.py -v`
Expected: FAIL — `remove_file` deletes the shared backup source, so the sibling test's `.shared.rc` is gone and the "still feeds" message is missing.

- [ ] **Step 3: Add `remove_destination`**

In `src/mackup_ng/application.py`, next to `remove_file`:

```python
def remove_destination(
    self,
    source: str,
    dest: str,
    siblings: int,
) -> dict[str, int]:
    """Remove one destination; drop the source only when nothing else uses it.

    ``siblings`` is the number of other live destinations fed by ``source``.
    """
    stats = self.new_stats()
    home_filepath = os.path.join(os.environ["HOME"], dest)
    targets = [home_filepath]
    if siblings == 0:
        targets.append(os.path.join(self.mackup.mackup_folder, source))

    if self.verbose:
        for filepath in targets:
            self._print(f"Deleting\n  {filepath} ...")

    if self.dry_run:
        stats["deleted"] += 1
        return stats

    for filepath in targets:
        if not os.path.lexists(filepath):
            continue
        try:
            utils.delete(filepath)
        except PermissionError as e:
            self._print(
                f"Error: Unable to delete file {filepath} due to permission issue: {e}",
            )
            stats["errors"] += 1

    if stats["errors"] == 0:
        self.record_deleted_file(dest)
        stats["deleted"] += 1
    return stats
```

- [ ] **Step 4: Rewrite the `rm` branch to use the resolved plan**

In `src/mackup_ng/main.py`, replace the `managed_paths` construction with plan-derived data and call the new method:

```python
        rm_pairs, _ = build_sync_plan(app_db, mckp.get_apps_to_backup())
        rm_groups, _ = mapping.group_by_source(rm_pairs)
        managed_paths: dict[str, tuple[str, tuple[str, str]]] = {}
        for pair in rm_pairs:
            managed_paths.setdefault(
                ApplicationProfile.normalize_relative_path(pair.dest),
                (pair.owner_app, (pair.dest, pair.source)),
            )
```

Keep `get_requested_path_candidates`, `escapes_home` and the descendant lookup exactly as they are — they already work on `(local, backup)` tuples. Replace the removal call at the end of the loop:

```python
matching_app_name, matching_mapping = match
local_filename, backup_filename = matching_mapping
pretty_name = app_db.get_name(matching_app_name)
siblings = [
    dest for dest in rm_groups.get(backup_filename, []) if dest != local_filename
]
app = ApplicationProfile(mckp, set(), dry_run, verbose)
print_app_header(matching_app_name, pretty_name)
app_stats = app.remove_destination(
    backup_filename,
    local_filename,
    len(siblings),
)
rm_action = get_action_label(app_stats)
if rm_action is not None:
    print(
        utils.colorize_message(
            f"{rm_action} {local_filename} ({pretty_name})",
        ),
    )
if siblings:
    print(
        utils.colorize_message(
            f"{backup_filename} still feeds {len(siblings)} destination(s)",
        ),
    )
```

Note: for a file *inside* a managed directory the descendant lookup produces a synthetic pair that is not in `rm_groups`, so `siblings` is empty and both sides are deleted — the current behavior, which the existing `tests/test_cli.py` nested-removal test covers.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_rm_destinations.py tests/test_cli.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
uv run pytest
uv run ruff check src tests
uv run mypy src
git add src/mackup_ng/application.py src/mackup_ng/main.py tests/test_rm_destinations.py
git commit -m "feat: scope rm to a single destination of a fanout group"
```

---

### Task 8: Retire `sync_files` and document the model

**Files:**
- Modify: `src/mackup_ng/application.py` (delete `sync_files`, `sync_directory_entries`, `sync_directory_entries_one_way`, `apply_deleted_files`, `remove_file` if unused), `tests/test_application.py`, `AGENTS.md`, `README.md`, `doc/ARCHITECTURE.md`
- Test: existing suites

**Interfaces:**
- Consumes: everything from Tasks 1–7.
- Produces: no new API.

- [ ] **Step 1: Find the dead code**

Run: `uv run rg -n "sync_files|sync_directory_entries|apply_deleted_files|remove_file" src tests`
Expected: only `tests/test_application.py` still calls `sync_files`; `main.py` no longer references any of them.

- [ ] **Step 2: Port the old tests to the group API**

In `tests/test_application.py`, replace each `self.app_profile.sync_files()` call with the equivalent group call. For example, `test_sync_files_merges_directories_by_file_mtime` becomes:

```python
        stats = self.app_profile.sync_group(".testfolder", [".testfolder"])
```

and `test_sync_files_ignores_missing_file_on_both_sides` becomes:

```python
        stats = self.app_profile.sync_group(".testfile", [".testfile"])
        assert not any(stats.values())
```

Rename the test methods from `test_sync_files_*` to `test_sync_group_*` so the names match what they exercise. Delete `test_files_are_sorted_for_deterministic_processing` — the constructor no longer sorts a set; ordering is the resolver's job and Task 2 covers it.

- [ ] **Step 3: Run the tests**

Run: `uv run pytest tests/test_application.py -v`
Expected: PASS

- [ ] **Step 4: Delete the superseded methods**

Remove `sync_files`, `sync_directory_entries`, `sync_directory_entries_one_way` and `apply_deleted_files` from `ApplicationProfile`. Keep `remove_file` only if `rg` still finds a caller; otherwise remove it too. Simplify the constructor, which no longer needs to build `file_entries`:

```python
    def __init__(
        self,
        mackup: Mackup,
        files: set[str] | set[tuple[str, str]] | list[str] | list[tuple[str, str]],
        dry_run: bool,
        verbose: bool,
    ) -> None:
        """Create an ApplicationProfile bound to a Mackup storage folder.

        ``files`` is kept for callers that still pass a declaration list; the
        sync engine works on groups handed to :meth:`sync_group`.
        """
        assert isinstance(mackup, Mackup)
        self.mackup: Mackup = mackup
        self.files = sorted(str(item) for item in files)
        self.dry_run: bool = dry_run
        self.verbose: bool = verbose
```

- [ ] **Step 5: Run the whole gate**

Run: `make check`
Expected: ruff, mypy, ty and pytest all pass.

- [ ] **Step 6: Update the documentation**

In `AGENTS.md`, extend the `### Explicit local → backup mapping ([mapped_files])` section (currently at `AGENTS.md:96-112`) with:

```markdown
The model is destination-keyed: the local path is the unique key, the backup
path is the source. Two consequences:

- **Fanout** — several keys may share one backup value, so one backup file
  feeds several local files. All members of the group are peers: the newest
  mtime wins and is copied to every other member; directories merge entry by
  entry, so all members converge to the union of their contents.
- **Override** — when a later config claims a destination already claimed by
  an earlier one, the earlier pair is dropped. Read order decides: stock
  configs (alphabetical), then `$XDG_CONFIG_HOME/mackup/applications`, then
  `~/.mackup/applications`; within a file, `files` then `[mapped_files]` in
  declaration order. A backup file left with no destination is untouched and
  reported by `mackup sync -v`.
- `mackup rm <path>` removes one destination and tombstones it; the backup
  source is deleted only when no destination is left.
```

In `README.md`, extend section 7 (`### 7. Explicit local → backup mapping ([mapped_files])`, around `README.md:871`) with a fanout example and the read-order rule:

```markdown
One backup file can feed several local files — repeat the value:

```toml
[mapped_files]
".config/app/work.profile/user.js"     = ".config/app/profile/user.js"
".config/app/personal.profile/user.js" = ".config/app/profile/user.js"
```

All members of such a group are peers: whichever copy you edited last wins and
is propagated to the others on the next `mackup sync`.

Destinations are unique. If a later config maps the same local path to a
different backup file, the earlier mapping is dropped — that is how you
overwrite a stock config with your own. Configs are read stock first, then
`$XDG_CONFIG_HOME/mackup/applications`, then `~/.mackup/applications`, so your
own files always win. Run `mackup sync -v` to see which mappings were
overridden and which backup files are left without a destination.
```

In `doc/ARCHITECTURE.md`, update the Sync Flow block (`doc/ARCHITECTURE.md:136-155`) to describe plan building:

```text
User runs: mackup-ng sync
    ↓
main.py parses command
    ↓
config.py loads .mackup.cfg
    ↓
appsdb.py loads application definitions in precedence order
    ↓
mapping.py resolves (source, destination) pairs — later pairs win the
destination, sources group into fanout groups
    ↓
application.py for each group:
    - Deletes tombstoned destinations
    - Picks the newest member and copies it to the others
    - Merges directory members entry by entry
    ↓
Files now in: ~/Dropbox/Mackup/ (or chosen storage)
```

Also add `mapping.py` to the File Structure listing in that document.

- [ ] **Step 7: Commit**

```bash
make check
git add -A
git commit -m "refactor: drop pairwise sync engine and document the mapping model"
```

---

## Self-Review Notes

Spec coverage check:

- Model, uniqueness rule, derived structures → Task 1.
- Read order, tiers, in-file order, brace determinism → Task 2.
- File group sync (peers, newest wins) → Task 3.
- Directory union across N members → Task 4.
- Global (cross-app) resolution, tombstone filtering, owner attribution → Task 5.
- Orphaned sources untouched + `-v` reporting, `show` fanout → Task 6.
- Destination-scoped `rm`, source deleted only when no destination remains → Task 7.
- Compatibility cleanup and docs → Task 8.
