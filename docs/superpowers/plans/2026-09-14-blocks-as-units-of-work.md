# Blocks as Units of Work Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a config an ordered sequence of units of work, so a block may carry `files`, an action, or both — giving file lists the whole `[when]` condition vocabulary without inventing a second mechanism.

**Architecture:** `ApplicationsDatabase` builds, per config, an ordered list of `Unit` objects numbered by slot in final execution order (`pre` blocks, the top-level unit, `during` blocks, `post` blocks). Each unit's `[when]` is evaluated once at build time. `mapping.Pair` carries `owner_slot` so the sync loop can walk a config's units in order, syncing each unit's files and then running its action.

**Tech Stack:** Python 3.12+, `tomllib`, pytest, uv.

**Spec:** `docs/superpowers/specs/2026-09-14-blocks-as-units-of-work-design.md`

## Global Constraints

- Python 3.12+. Test command: `uv run pytest`. Full gate: `make check` (rumdl, ruff, mypy, ty, pytest) must be fully green, **zero ruff errors**.
- **Every import at module top level.** The ruff config selects `PLC0415`; this has already had to be fixed four times on this repo. No `noqa`, no ruff-config edits.
- Warnings use the established pattern `print(utils.colorize_message("Warning: ..."))`. `utils` has no `warn()` helper and none may be added.
- Baseline at the start: **307 tests pass**, `make check` green, on branch `master`.
- Work directly on `master` — the user asked for this explicitly. Commit after every task. Do not push; the user pushes.
- `phase` values are `"pre"`, `"during"`, `"post"`; the default is `"during"` (it was `"post"`).
- Slots are numbered 0, 1, 2 … in **final execution order across all phases**, not within a phase.
- Within a unit: files sync first, then the action runs.
- Blocks support `files` only — not `mapped_files`. Only the top-level unit carries `mapped_files`.

---

### Task 1: The `not_os` condition

**Files:**
- Modify: `src/mackup_ng/conditions.py:25-49` (`_one`), `:51-61` (`CONDITION_KEYS`)
- Test: `tests/test_conditions.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `not_os` as a recognized key in `conditions.CONDITION_KEYS`, evaluated as `hooks.os_kind() not in _as_list(value)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_conditions.py`:

```python
def test_not_os_is_the_complement_of_os(monkeypatch):
    monkeypatch.setattr(hooks, "os_kind", lambda: "linux")

    assert conditions.block_passes({"when": {"not_os": "macos"}})
    assert not conditions.block_passes({"when": {"not_os": "linux"}})


def test_not_os_accepts_a_list(monkeypatch):
    monkeypatch.setattr(hooks, "os_kind", lambda: "windows")

    assert conditions.block_passes({"when": {"not_os": ["macos", "linux"]}})
    assert not conditions.block_passes(
        {"when": {"not_os": ["macos", "windows"]}},
    )


def test_not_os_keeps_android_where_os_list_would_drop_it(monkeypatch):
    """The reason not_os exists: os = ["linux", "windows"] silently excludes
    android, which os_kind() reports separately."""
    monkeypatch.setattr(hooks, "os_kind", lambda: "android")

    assert conditions.block_passes({"when": {"not_os": "macos"}})
    assert not conditions.block_passes({"when": {"os": ["linux", "windows"]}})


def test_not_os_is_a_recognized_key():
    assert conditions.unrecognized_keys({"not_os": "macos"}) == []
```

Ensure `tests/test_conditions.py` imports both modules at the top of the file:

```python
from mackup_ng import conditions, hooks
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_conditions.py -k not_os -v`
Expected: FAIL — `test_not_os_is_a_recognized_key` fails because `not_os` is unrecognized, and the complement tests pass vacuously today (an unknown key returns `True` from `_one`), so `assert not conditions.block_passes(...)` fails.

- [ ] **Step 3: Implement**

In `src/mackup_ng/conditions.py`, inside `_one`, directly after the `os` branch:

```python
    if key == "not_os":
        return hooks.os_kind() not in _as_list(value)
```

and add `"not_os"` to `CONDITION_KEYS`, immediately after `"os"`:

```python
CONDITION_KEYS = (
    "os",
    "not_os",
    "arch",
    "marker",
    "not_marker",
    "command",
    "gui",
    "exists",
    "not_exists",
    "env",
)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_conditions.py -v`
Expected: PASS.

Run: `uv run pytest`
Expected: PASS, 311 tests (307 + 4 new).

- [ ] **Step 5: Commit**

```bash
git add src/mackup_ng/conditions.py tests/test_conditions.py
git commit -m "feat(conditions): add not_os

Completes the row the module already has for marker/not_marker and
exists/not_exists. os = [\"linux\", \"windows\"] silently excludes android,
which os_kind() reports separately; not_os = \"macos\" does not."
```

---

### Task 2: Units in `ApplicationsDatabase`

**Files:**
- Modify: `src/mackup_ng/appsdb.py:446-465` (the top-block assembly), `:485-540` (path collection), `:592-620` (accessors)
- Modify: `src/mackup_ng/main.py:150`, `:262`, `:379` and `src/mackup_ng/info.py:168` (call sites of `get_file_mappings`)
- Test: `tests/test_units.py` (create)

**Interfaces:**
- Consumes: `conditions.block_passes({"when": ...})` and `conditions.unrecognized_keys(when)` from `conditions.py`; `blocks.block_action(block)` from `blocks.py`.
- Produces:
  - `appsdb.Unit` — a frozen dataclass with `slot: int`, `when: dict`, `passed: bool`, `mappings: tuple[tuple[str, str], ...]` of `(local, backup)`, `block: dict | None`.
  - `ApplicationsDatabase.get_units(name) -> list[Unit]` — every unit, in slot order, whether or not it passed.
  - `ApplicationsDatabase.get_file_mappings(name) -> list[tuple[str, str, int]]` — `(local, backup, slot)`, **only from units that passed**. Arity changes from 2 to 3.
  - `ApplicationsDatabase.get_blocks(name) -> list[dict]` — unchanged signature; now the action blocks of passing units, in slot order.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_units.py`:

```python
"""A config is an ordered sequence of units, numbered in execution order."""

import os
import shutil
import tempfile
import unittest

from mackup_ng.appsdb import ApplicationsDatabase


class TestUnits(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="mackup_units_home_")
        self._orig = {
            key: os.environ.get(key)
            for key in ("HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME")
        }
        os.environ["HOME"] = self.home
        os.environ["XDG_CONFIG_HOME"] = os.path.join(self.home, ".config")
        os.environ["XDG_DATA_HOME"] = os.path.join(self.home, ".local", "share")
        os.environ["XDG_STATE_HOME"] = os.path.join(self.home, ".local", "state")
        self.apps_dir = os.path.join(self.home, ".config", "mackup", "applications")
        os.makedirs(self.apps_dir, exist_ok=True)

    def tearDown(self):
        for key, value in self._orig.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(self.home, ignore_errors=True)

    def _write(self, name, body):
        with open(os.path.join(self.apps_dir, f"{name}.toml"), "w") as handle:
            handle.write(body)

    def test_slots_are_numbered_in_execution_order(self):
        """pre block, then the top-level unit, then during, then post."""
        self._write(
            "ordered",
            'name = "Ordered"\n'
            'files = [".toplevel"]\n'
            "\n"
            "[[block]]\n"
            'phase = "post"\n'
            'files = [".late"]\n'
            "\n"
            "[[block]]\n"
            'phase = "pre"\n'
            'files = [".early"]\n'
            "\n"
            "[[block]]\n"
            'files = [".middle"]\n',
        )
        units = ApplicationsDatabase().get_units("ordered")

        locals_by_slot = [
            [local for local, _backup in unit.mappings] for unit in units
        ]
        assert [unit.slot for unit in units] == [0, 1, 2, 3]
        assert locals_by_slot == [
            [".early"],
            [".toplevel"],
            [".middle"],
            [".late"],
        ]

    def test_a_block_defaults_to_during(self):
        self._write(
            "defaulted",
            'name = "Defaulted"\nfiles = [".top"]\n\n[[block]]\nfiles = [".after"]\n',
        )
        units = ApplicationsDatabase().get_units("defaulted")

        assert [[local for local, _ in u.mappings] for u in units] == [
            [".top"],
            [".after"],
        ]

    def test_a_failing_unit_contributes_no_mappings(self):
        self._write(
            "gated",
            'name = "Gated"\n'
            'files = [".always"]\n'
            "\n"
            "[[block]]\n"
            'files = [".mac-only"]\n'
            "[block.when]\n"
            'os = "definitely-not-this-os"\n',
        )
        db = ApplicationsDatabase()

        units = db.get_units("gated")
        assert [unit.passed for unit in units] == [True, False]
        assert db.get_file_mappings("gated") == [(".always", ".always", 0)]

    def test_file_mappings_carry_their_slot(self):
        self._write(
            "slotted",
            'name = "Slotted"\nfiles = [".a"]\n\n[[block]]\nfiles = [".b"]\n',
        )

        assert ApplicationsDatabase().get_file_mappings("slotted") == [
            (".a", ".a", 0),
            (".b", ".b", 1),
        ]

    def test_a_block_may_carry_both_files_and_an_action(self):
        self._write(
            "both",
            'name = "Both"\n'
            "\n"
            "[[block]]\n"
            'files = [".thing"]\n'
            "[block.chmod]\n"
            'path = "~/.thing"\n'
            'file_mode = "600"\n',
        )
        units = ApplicationsDatabase().get_units("both")

        unit = units[-1]
        assert [local for local, _ in unit.mappings] == [".thing"]
        assert unit.block is not None
        assert "chmod" in unit.block

    def test_get_blocks_returns_actions_of_passing_units_in_slot_order(self):
        self._write(
            "acts",
            'name = "Acts"\n'
            "\n"
            "[[block]]\n"
            'phase = "post"\n'
            "[block.run]\n"
            'script = "echo late"\n'
            "\n"
            "[[block]]\n"
            'phase = "pre"\n'
            "[block.run]\n"
            'script = "echo early"\n',
        )
        scripts = [
            b["run"]["script"] for b in ApplicationsDatabase().get_blocks("acts")
        ]

        assert scripts == ["echo early", "echo late"]

    def test_unknown_phase_warns_and_is_treated_as_during(self, ):
        self._write(
            "badphase",
            'name = "Bad"\nfiles = [".top"]\n\n[[block]]\n'
            'phase = "whenever"\nfiles = [".other"]\n',
        )
        units = ApplicationsDatabase().get_units("badphase")

        assert [[local for local, _ in u.mappings] for u in units] == [
            [".top"],
            [".other"],
        ]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_units.py -v`
Expected: FAIL — `AttributeError: 'ApplicationsDatabase' object has no attribute 'get_units'`.

- [ ] **Step 3: Add the `Unit` dataclass**

At the top of `src/mackup_ng/appsdb.py`, after the existing imports, add `from dataclasses import dataclass` to the import block and define:

```python
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
```

- [ ] **Step 4: Build the units while parsing**

In `src/mackup_ng/appsdb.py`, replace the top-block assembly (the block beginning `# The whole top level is one block:` and ending `self.app_blocks[app_name] = cfg_blocks`) with ordering that produces the unit sequence:

```python
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
```

Then, where `config_paths` is collected today, build every unit instead. Replace the whole `config_paths = next(...)` / `for path in config_paths:` / `for src, dest in data.get("mapped_files", {}).items():` region with:

```python
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
                when = {} if is_top else dict(entry.get("when", {}))
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
                raw_paths = top_paths if is_top else entry.get("files", [])
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
```

Initialise `self.app_units: dict[str, list[Unit]] = {}` beside the other per-app dicts in `__init__`.

Note the `mapped_files` loop keeps its original `_pair_to_exprs` call and belongs to the top-level unit only, per the Global Constraints.

- [ ] **Step 5: Add the accessors**

In `src/mackup_ng/appsdb.py`, replace `get_file_mappings` and add `get_units`:

```python
    def get_units(self, name: str) -> list[Unit]:
        """Return the config's units in slot order, passing or not."""
        return list(self.app_units.get(name, []))

    def get_file_mappings(self, name: str) -> list[tuple[str, str, int]]:
        """Return (local, backup, slot) triples of an application, in read order.

        Only units whose conditions hold contribute mappings.
        """
        return list(self.app_file_mappings[name])
```

- [ ] **Step 6: Update the four call sites to the new arity**

`src/mackup_ng/main.py` around line 150:

```python
        for local, backup, _slot in app_db.get_file_mappings(app_name)
```

`src/mackup_ng/main.py` around line 262 — find the loop over `mappings` and unpack three values; the third is unused there, so name it `_slot`.

`src/mackup_ng/main.py` around line 379:

```python
                for _local, backup, _slot in app_db.get_file_mappings(app_name):
```

`src/mackup_ng/info.py` around line 168:

```python
        for local, backup, _slot in app_db.get_file_mappings(app_name):
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest tests/test_units.py -v`
Expected: PASS, 7 tests.

Run: `uv run pytest`
Expected: PASS. If any test fails on mapping arity, a call site was missed — `grep -rn "get_file_mappings" src/ tests/`.

Run: `make check`
Expected: green.

- [ ] **Step 8: Commit**

```bash
git add src/mackup_ng/appsdb.py src/mackup_ng/main.py src/mackup_ng/info.py tests/test_units.py
git commit -m "feat(appsdb): parse a config into ordered units

A config becomes a sequence of units numbered in execution order: pre
blocks, the top-level unit, during blocks, post blocks. Each unit's [when]
is evaluated once here, and only passing units contribute mappings, so
global ownership resolution keeps working on a flat list of pairs.

get_file_mappings now yields (local, backup, slot)."
```

---

### Task 3: `owner_slot` on `mapping.Pair`

**Files:**
- Modify: `src/mackup_ng/mapping.py:22-28` (`Pair`), `:76-86` (`group_owners`)
- Modify: `src/mackup_ng/main.py:144-151` (pair construction), `:401-404` (owner grouping)
- Test: `tests/test_mapping_model.py`

**Interfaces:**
- Consumes: `get_file_mappings(name) -> list[tuple[str, str, int]]` from Task 2.
- Produces: `mapping.Pair(source, dest, owner_app, owner_slot)`; `mapping.group_owners(pairs) -> dict[str, tuple[str, int]]` — the source now maps to `(app, slot)`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_mapping_model.py`:

```python
def test_group_owners_reports_the_winning_pair_slot():
    """A contested destination is attributed to the last pair in read order —
    and now to the slot within that config, which is what orders the sync."""
    pairs = [
        mapping.Pair(source="s", dest="d", owner_app="weak", owner_slot=0),
        mapping.Pair(source="s", dest="d2", owner_app="strong", owner_slot=3),
    ]

    assert mapping.group_owners(pairs) == {"s": ("strong", 3)}
```

Check the file's existing import line; it must already import `mapping` at module top level.

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_mapping_model.py -k owner_slot -v`
Expected: FAIL — `TypeError: Pair.__init__() got an unexpected keyword argument 'owner_slot'`.

- [ ] **Step 3: Add the field and widen `group_owners`**

In `src/mackup_ng/mapping.py`:

```python
@dataclass(frozen=True)
class Pair:
    """One managed mapping: backup ``source`` feeds local ``dest``."""

    source: str
    dest: str
    owner_app: str
    owner_slot: int
```

```python
def group_owners(pairs: Sequence[Pair]) -> dict[str, tuple[str, int]]:
    """Map each source to the (app, slot) that owns its last pair.

    A fanout group can gather destinations declared by several configs. It is
    attributed to the config that claimed the *last* destination in read order
    — the strongest one, since read order is also override order. The slot
    says which of that config's units declared it, which is what puts the
    group in the right place in the sync order. Feed this the live
    (non-tombstoned) pairs so a group is never attributed to a config that
    contributes no destination.
    """
    return {pair.source: (pair.owner_app, pair.owner_slot) for pair in pairs}
```

- [ ] **Step 4: Build pairs with their slot**

In `src/mackup_ng/main.py`, the `entries` comprehension around line 144:

```python
    entries = [
        mapping.Pair(
            source=backup,
            dest=local,
            owner_app=app_name,
            owner_slot=slot,
        )
        for app_name in app_db.get_app_order()
        if app_name in apps_to_sync
        and app_db.app_has_sync(app_name)
        and app_db.config_enabled(app_name)
        for local, backup, slot in app_db.get_file_mappings(app_name)
    ]
```

and the owner grouping around line 401:

```python
        owners = mapping.group_owners(live_pairs)
        groups_by_slot: dict[tuple[str, int], list[tuple[str, list[str]]]] = {}
        for source, dests in groups.items():
            groups_by_slot.setdefault(owners[source], []).append((source, dests))
```

Leave the per-config loop reading `groups_by_owner` for now — Task 4 rewrites it. To keep this task's suite green, add directly below:

```python
        groups_by_owner: dict[str, list[tuple[str, list[str]]]] = {}
        for (app, _slot), owned_groups in groups_by_slot.items():
            groups_by_owner.setdefault(app, []).extend(owned_groups)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_mapping_model.py -v`
Expected: PASS.

Run: `uv run pytest` and `make check`
Expected: green.

- [ ] **Step 6: Commit**

```bash
git add src/mackup_ng/mapping.py src/mackup_ng/main.py tests/test_mapping_model.py
git commit -m "feat(mapping): carry the declaring unit's slot on each pair

Ownership resolution is unchanged — still a flat list resolved in read
order — but the winner now also reports which of its units declared the
destination, which is what lets the sync loop run units in order."
```

---

### Task 4: Apply one unit's action with config-wide service deferral

**Files:**
- Modify: `src/mackup_ng/blocks.py:492-513` (`apply_blocks`)
- Test: `tests/test_blocks.py`

**Interfaces:**
- Consumes: `conditions.block_passes(block)`, `blocks.apply_block(block, env_files, dry_run, pending_starts)`.
- Produces: `blocks.apply_unit_action(block, env_files, dry_run, pending_starts) -> Counter` — applies one block's action, honouring its `[when]`, deferring service starts into the caller-owned `pending_starts` set; and `blocks.flush_pending_starts(pending_starts) -> None`, which starts every deferred service and clears the set.

`apply_blocks` is kept, unchanged in behaviour, because `mackup apply` still uses it until Task 6.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_blocks.py`:

```python
def test_three_blocks_of_one_config_restart_a_service_once(monkeypatch):
    """The deferral must span the whole config, not one phase: three blocks
    that each declare restart_service = "x" are one stop and one start."""
    calls = []
    monkeypatch.setattr(blocks, "svc_stop", lambda svc: calls.append(("stop", svc)))
    monkeypatch.setattr(blocks, "svc_start", lambda svc: calls.append(("start", svc)))

    cfg_blocks = [
        {"restart_service": "x", "run": {"script": "true"}},
        {"restart_service": "x", "run": {"script": "true"}},
        {"restart_service": "x", "run": {"script": "true"}},
    ]
    pending: set[str] = set()
    for block in cfg_blocks:
        blocks.apply_unit_action(block, [], dry_run=False, pending_starts=pending)
    blocks.flush_pending_starts(pending)

    assert calls.count(("stop", "x")) == 1
    assert calls.count(("start", "x")) == 1
    assert calls[-1] == ("start", "x")


def test_apply_unit_action_honours_the_block_condition(tmp_path, monkeypatch):
    monkeypatch.setattr(blocks, "svc_stop", lambda svc: None)
    monkeypatch.setattr(blocks, "svc_start", lambda svc: None)
    marker = tmp_path / "should-not-exist"

    block = {
        "when": {"os": "definitely-not-this-os"},
        "run": {"script": f'touch "{marker}"'},
    }
    pending: set[str] = set()
    tally = blocks.apply_unit_action(block, [], dry_run=False, pending_starts=pending)

    assert tally == Counter()
    assert not marker.exists(), "the action ran despite its condition failing"
```

Ensure `tests/test_blocks.py` imports `Counter` and `blocks` at module top level:

```python
from collections import Counter

from mackup_ng import blocks
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_blocks.py -k "restart_a_service_once or honours_the_block_condition" -v`
Expected: FAIL — `AttributeError: module 'mackup_ng.blocks' has no attribute 'apply_unit_action'`.

- [ ] **Step 3: Implement**

In `src/mackup_ng/blocks.py`, add above `apply_blocks`:

```python
def apply_unit_action(
    block: dict,
    env_files: list[str],
    dry_run: bool,
    pending_starts: set[str],
) -> Counter:
    """Apply one unit's action; return a Counter action -> changes.

    Service starts are deferred into ``pending_starts``, which the caller owns
    and flushes once per config with :func:`flush_pending_starts`. A block that
    stops a service, and the next one that needs it stopped too, must not see
    it restarted in between.
    """
    tally: Counter = Counter()
    if not conditions.block_passes(block):
        return tally
    action, count = apply_block(block, env_files, dry_run, pending_starts)
    if action and count:
        tally[action] += count
    return tally


def flush_pending_starts(pending_starts: set[str]) -> None:
    """Start every service deferred so far and clear the set."""
    for svc in sorted(pending_starts):
        svc_start(svc)
    pending_starts.clear()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_blocks.py -v`
Expected: PASS.

Run: `uv run pytest` and `make check`
Expected: green.

- [ ] **Step 5: Commit**

```bash
git add src/mackup_ng/blocks.py tests/test_blocks.py
git commit -m "feat(blocks): apply a single unit's action, deferring service starts

The caller now owns pending_starts so the deferral can span a whole config
instead of one phase — three blocks restarting the same service stay one
stop and one start once the sync loop applies units one at a time."
```

---

### Task 5: Sync in slot order

**Files:**
- Modify: `src/mackup_ng/main.py:406-455` (the per-config loop)
- Test: `tests/test_unit_order.py` (create)

**Interfaces:**
- Consumes: `app_db.get_units(name)`, `app_db.get_file_mappings(name)` (Task 2); `groups_by_slot` keyed by `(app, slot)` (Task 3); `blocks.apply_unit_action` and `blocks.flush_pending_starts` (Task 4).
- Produces: no new API. The per-config loop walks units in slot order, syncing each unit's groups then applying its action.

- [ ] **Step 1: Write the failing test**

Create `tests/test_unit_order.py`:

```python
"""A config's units execute in slot order, files before action."""

import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from mackup_ng import utils
from mackup_ng.main import main

from .conftest import write_config

# main() reads sys.argv and takes no arguments; tests/test_cli.py drives it
# with patch("sys.argv", [...]). Follow that, not main(["sync"]).


class TestUnitOrder(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="mackup_order_home_")
        self.storage = tempfile.mkdtemp(prefix="mackup_order_store_")
        self._orig = {
            key: os.environ.get(key)
            for key in ("HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME")
        }
        os.environ["HOME"] = self.home
        os.environ["XDG_CONFIG_HOME"] = os.path.join(self.home, ".config")
        os.environ["XDG_DATA_HOME"] = os.path.join(self.home, ".local", "share")
        os.environ["XDG_STATE_HOME"] = os.path.join(self.home, ".local", "state")
        os.makedirs(os.path.join(self.storage, "Mackup"), exist_ok=True)
        self.config_path = os.path.join(
            self.home, ".config", "mackup", "config.toml",
        )
        write_config(
            self.config_path,
            storage_path=self.storage,
            sync=["ordered"],
        )
        self.apps_dir = os.path.join(self.home, ".config", "mackup", "applications")
        os.makedirs(self.apps_dir, exist_ok=True)
        utils.FORCE_YES = True
        self.fetch_patcher = patch("mackup_ng.update.fetch_latest", return_value=None)
        self.fetch_patcher.start()
        self.addCleanup(self.fetch_patcher.stop)

    def tearDown(self):
        for key, value in self._orig.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(self.home, ignore_errors=True)
        shutil.rmtree(self.storage, ignore_errors=True)
        utils.FORCE_YES = False

    def _write_app(self, body):
        with open(os.path.join(self.apps_dir, "ordered.toml"), "w") as handle:
            handle.write(body)

    def test_a_unit_action_sees_the_files_that_unit_just_synced(self):
        """Files first, then the action — within one unit."""
        backup = os.path.join(self.storage, "Mackup", ".unitfile")
        with open(backup, "w") as handle:
            handle.write("from backup\n")
        marker = os.path.join(self.home, "saw-it")

        self._write_app(
            'name = "Ordered"\n'
            "\n"
            "[[block]]\n"
            'files = [".unitfile"]\n'
            "[block.run]\n"
            f'script = \'test -f "$HOME/.unitfile" && touch "{marker}"\'\n',
        )

        with patch("sys.argv", ["mackup", "sync"]):
            main()

        assert os.path.exists(os.path.join(self.home, ".unitfile"))
        assert os.path.exists(marker), "the action ran before its unit's files"

    def test_a_gated_unit_syncs_nothing_and_does_not_run(self):
        backup = os.path.join(self.storage, "Mackup", ".gatedfile")
        with open(backup, "w") as handle:
            handle.write("from backup\n")
        marker = os.path.join(self.home, "should-not-exist")

        self._write_app(
            'name = "Ordered"\n'
            "\n"
            "[[block]]\n"
            'files = [".gatedfile"]\n'
            "[block.when]\n"
            'os = "definitely-not-this-os"\n'
            "[block.run]\n"
            f'script = \'touch "{marker}"\'\n',
        )

        with patch("sys.argv", ["mackup", "sync"]):
            main()

        assert not os.path.exists(os.path.join(self.home, ".gatedfile"))
        assert not os.path.exists(marker)

    def test_a_pre_block_runs_before_the_top_level_files(self):
        backup = os.path.join(self.storage, "Mackup", ".topfile")
        with open(backup, "w") as handle:
            handle.write("from backup\n")
        marker = os.path.join(self.home, "pre-ran-first")

        self._write_app(
            'name = "Ordered"\n'
            'files = [".topfile"]\n'
            "\n"
            "[[block]]\n"
            'phase = "pre"\n'
            "[block.run]\n"
            f'script = \'test ! -f "$HOME/.topfile" && touch "{marker}"\'\n',
        )

        with patch("sys.argv", ["mackup", "sync"]):
            main()

        assert os.path.exists(os.path.join(self.home, ".topfile"))
        assert os.path.exists(marker), "the pre block ran after the top-level files"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_unit_order.py -v`
Expected: FAIL — the first test fails because today a block's `files` key is ignored entirely, so `.unitfile` never syncs.

- [ ] **Step 3: Rewrite the per-config loop**

In `src/mackup_ng/main.py`, replace the body between `env_files = app_db.get_env_files(app_name)` and `report_config(pretty_name, stats, tally)` with a walk over units. Delete the `groups_by_owner` compatibility shim added in Task 3 Step 4, and the two `blocks.apply_blocks(cfg_blocks, "pre"/"post", ...)` calls:

```python
            env_files = app_db.get_env_files(app_name)
            pretty_name = app_db.get_name(app_name)
            units = app_db.get_units(app_name)

            tally: Counter = Counter()
            pending_starts: set[str] = set()
            stats: dict[str, int] | None = None
            syncing = app_name in to_backup and app_db.app_has_sync(app_name)
            if syncing:
                stats = ApplicationProfile.new_stats()
                # The config being synced adds its own ignores on top of the
                # global ignore files.
                app = ApplicationProfile(
                    mckp,
                    dry_run,
                    verbose,
                    ignore.load_globs() + tuple(app_db.get_ignore_patterns(app_name)),
                )
                header_printed = False

            try:
                for unit in units:
                    if not unit.passed:
                        continue
                    owned = groups_by_slot.get((app_name, unit.slot), [])
                    if syncing and owned:
                        if not header_printed:
                            print_app_header(app_name, pretty_name)
                            header_printed = True
                        for source, dests in owned:
                            group_stats = app.sync_group(source, dests)
                            for key, value in group_stats.items():
                                stats[key] += value
                            # What `mackup info` reports as the last sync of a
                            # destination: the outcome of the group it belongs to.
                            group_label = get_action_label(group_stats)
                            if group_label is not None:
                                now = time.time()
                                for dest in dests:
                                    log_entries[dest] = {
                                        "ts": now,
                                        "action": group_label,
                                        "source": source,
                                    }
                    if unit.block is not None:
                        tally += blocks.apply_unit_action(
                            unit.block,
                            env_files,
                            dry_run,
                            pending_starts,
                        )
            finally:
                blocks.flush_pending_starts(pending_starts)

            report_config(pretty_name, stats, tally)
```

Add `from collections import Counter` to `main.py`'s module-level imports if it is not already there.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_unit_order.py -v`
Expected: PASS, 3 tests.

Run: `uv run pytest`
Expected: PASS. Pay attention to `tests/test_config_conditions.py` and `tests/test_sync_groups.py` — they exercise ordering and fanout and are the likeliest to catch a mistake here.

Run: `make check`
Expected: green.

- [ ] **Step 5: Commit**

```bash
git add src/mackup_ng/main.py tests/test_unit_order.py
git commit -m "feat(sync): run a config's units in slot order

Each unit syncs its own files and then runs its own action, so a block can
carry a conditional file list. Service starts are deferred across the whole
config rather than per phase."
```

---

### Task 6: `mackup apply` walks units, actions only

**Files:**
- Modify: `src/mackup_ng/main.py` (the `apply` branch)
- Test: `tests/test_apply_units.py` (create)

**Interfaces:**
- Consumes: `app_db.get_units(name)`, `blocks.apply_unit_action`, `blocks.flush_pending_starts`.
- Produces: no new API.

- [ ] **Step 1: Write the failing test**

Create `tests/test_apply_units.py`:

```python
"""`mackup apply` runs actions and never syncs files."""

import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from mackup_ng import utils
from mackup_ng.main import main

from .conftest import write_config

# main() reads sys.argv and takes no arguments; tests/test_cli.py drives it
# with patch("sys.argv", [...]). Follow that, not main(["sync"]).


class TestApplyUnits(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="mackup_apply_home_")
        self.storage = tempfile.mkdtemp(prefix="mackup_apply_store_")
        self._orig = {
            key: os.environ.get(key)
            for key in ("HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME")
        }
        os.environ["HOME"] = self.home
        os.environ["XDG_CONFIG_HOME"] = os.path.join(self.home, ".config")
        os.environ["XDG_DATA_HOME"] = os.path.join(self.home, ".local", "share")
        os.environ["XDG_STATE_HOME"] = os.path.join(self.home, ".local", "state")
        os.makedirs(os.path.join(self.storage, "Mackup"), exist_ok=True)
        write_config(
            os.path.join(self.home, ".config", "mackup", "config.toml"),
            storage_path=self.storage,
            sync=["mixed"],
        )
        self.apps_dir = os.path.join(self.home, ".config", "mackup", "applications")
        os.makedirs(self.apps_dir, exist_ok=True)
        utils.FORCE_YES = True
        self.fetch_patcher = patch("mackup_ng.update.fetch_latest", return_value=None)
        self.fetch_patcher.start()
        self.addCleanup(self.fetch_patcher.stop)

    def tearDown(self):
        for key, value in self._orig.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(self.home, ignore_errors=True)
        shutil.rmtree(self.storage, ignore_errors=True)
        utils.FORCE_YES = False

    def test_apply_runs_the_action_and_leaves_the_files_alone(self):
        backup = os.path.join(self.storage, "Mackup", ".applyfile")
        with open(backup, "w") as handle:
            handle.write("from backup\n")
        marker = os.path.join(self.home, "action-ran")

        with open(os.path.join(self.apps_dir, "mixed.toml"), "w") as handle:
            handle.write(
                'name = "Mixed"\n'
                "\n"
                "[[block]]\n"
                'files = [".applyfile"]\n'
                "[block.run]\n"
                f'script = \'touch "{marker}"\'\n',
            )

        with patch("sys.argv", ["mackup", "apply"]):
            main()

        assert os.path.exists(marker), "apply did not run the action"
        assert not os.path.exists(os.path.join(self.home, ".applyfile")), (
            "apply synced files, which is exactly what it must not do"
        )
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_apply_units.py -v`
Expected: FAIL. Today `apply` calls `apply_blocks(..., "pre")` and `apply_blocks(..., "post")`; with the default phase now `during`, neither call matches, so the action never runs and the marker is absent.

This failure is itself the point: leaving `apply` on the old phase strings would silently stop running every existing block.

- [ ] **Step 3: Rewrite the `apply` branch**

Find the `elif args["apply"]:` branch in `src/mackup_ng/main.py` and replace its per-config body with:

```python
            env_files = app_db.get_env_files(app_name)
            tally: Counter = Counter()
            pending_starts: set[str] = set()
            try:
                for unit in app_db.get_units(app_name):
                    if not unit.passed or unit.block is None:
                        continue
                    tally += blocks.apply_unit_action(
                        unit.block,
                        env_files,
                        dry_run,
                        pending_starts,
                    )
            finally:
                blocks.flush_pending_starts(pending_starts)
```

keeping whatever reporting the branch already does around it.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_apply_units.py -v`
Expected: PASS.

Run: `uv run pytest` and `make check`
Expected: green.

- [ ] **Step 5: Commit**

```bash
git add src/mackup_ng/main.py tests/test_apply_units.py
git commit -m "feat(apply): walk units, run actions, never sync files

apply's documented contract is actions without file sync; with the default
phase now \"during\", its old pre/post calls would have matched nothing."
```

---

### Task 7: `mackup show` reports skipped units

**Files:**
- Modify: `src/mackup_ng/main.py:295-307` (the `show` branch's block listing)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `app_db.get_units(name)` (Task 2) and `conditions.failing({"when": unit.when})`, which returns only the conditions that do not hold.
- Produces: no new API.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli.py`, inside the existing test class, matching its established setup:

```python
    def test_show_names_a_unit_skipped_by_its_condition(self):
        with open(os.path.join(self.custom_apps_dir, "gated.toml"), "w") as handle:
            handle.write(
                'name = "Gated"\n'
                'files = [".always"]\n'
                "\n"
                "[[block]]\n"
                'files = [".mac-only"]\n'
                "[block.when]\n"
                'os = "definitely-not-this-os"\n',
            )

        output = self._run_and_capture(["show", "gated"])

        assert "conditions not met" in output
        assert "definitely-not-this-os" in output
```

`tests/test_cli.py` already captures output this way — follow it exactly:

```python
        buf = io.StringIO()
        with patch("sys.argv", ["mackup", "show", "gated"]), patch("sys.stdout", buf):
            main()
        output = buf.getvalue()
```

`io` and `patch` are already imported at the top of that file. `self.custom_apps_dir` already exists in its `setUp`.

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_cli.py -k skipped_by_its_condition -v`
Expected: FAIL — the skipped unit produces no output at all today.

- [ ] **Step 3: Implement**

In the `show` branch of `src/mackup_ng/main.py`, replace the `cfg_blocks = app_db.get_blocks(requested_app_name)` listing with a units listing:

```python
        units = app_db.get_units(requested_app_name)
        listed = [u for u in units if u.block is not None or not u.passed]
        if listed:
            print(bold("Units:"))
            for unit in listed:
                action = (
                    utils.style_text(
                        str(blocks.block_action(unit.block)),
                        color=utils.AnsiColor.CYAN,
                    )
                    if unit.block is not None
                    else utils.style_text("files only", color=utils.AnsiColor.GRAY)
                )
                if unit.passed:
                    print(f"{dash} slot {unit.slot}: {action}")
                else:
                    unmet = conditions.failing({"when": unit.when})
                    detail = ", ".join(f"{k}={v}" for k, v in sorted(unmet.items()))
                    print(
                        f"{dash} slot {unit.slot}: {action} — "
                        + utils.style_text(
                            f"conditions not met ({detail})",
                            color=utils.AnsiColor.GRAY,
                        ),
                    )
```

Ensure `conditions` is imported at the top of `main.py`; add `from . import conditions` to the existing import block if absent.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -v`
Expected: PASS.

Run: `uv run pytest` and `make check`
Expected: green.

- [ ] **Step 5: Commit**

```bash
git add src/mackup_ng/main.py tests/test_cli.py
git commit -m "feat(show): name units skipped by their conditions

A condition-gated block used to vanish from show with no trace, unlike a
config-level [when], which reports itself. Silent absence is the failure
mode this whole change exists to avoid."
```

---

### Task 8: Merge the 120 split configs back

**Files:**
- Modify: 120 files `src/mackup_ng/applications/<app>.toml`
- Delete: 120 files `src/mackup_ng/applications/<app>-macos.toml`
- Modify: `tests/test_macos_only_configs.py`

**Interfaces:**
- Consumes: block-level `files` and `[block.when]` from Task 2; `not_os` from Task 1.
- Produces: no API. The shipped configs stop using the `-macos.toml` convention.

- [ ] **Step 1: Rewrite the invariant test**

Replace `test_no_config_mixes_library_with_other_paths` and `test_split_macos_configs_are_gated` in `tests/test_macos_only_configs.py` with rules for the new shape. Keep `_config_paths`, `_all_configs` and `test_at_least_one_library_config_exists` as they are, and add a helper that reads a config's units as TOML:

```python
def _unit_tables(data: dict) -> list[dict]:
    """The config's file-bearing tables: the top level, then each block."""
    return [data, *[b for b in data.get("block", []) if isinstance(b, dict)]]


def _table_paths(table: dict) -> list[str]:
    return [p for p in table.get("files", []) or [] if isinstance(p, str)]


def test_no_library_path_is_offered_to_other_platforms():
    """Every ~/Library path sits in a config or a block gated to macOS."""
    offenders = []
    for path, data in _all_configs():
        config_gated = data.get("when", {}).get("os") == "macos"
        for table in _unit_tables(data):
            if not any(p.startswith(LIBRARY_PREFIX) for p in _table_paths(table)):
                continue
            block_gated = table.get("when", {}).get("os") == "macos"
            if not (config_gated or block_gated):
                offenders.append(path.name)
                break

    assert offenders == [], (
        f"{len(offenders)} config(s) offer a ~/Library path on every platform:"
        f" {offenders[:10]}"
    )


def test_no_split_macos_files_remain():
    """The <app>-macos.toml convention is gone; blocks replaced it."""
    leftovers = [path.name for path, _ in _all_configs() if path.stem.endswith("-macos")]

    assert leftovers == [], f"still split: {leftovers[:10]}"
```

Delete the now-unused `@pytest.mark.parametrize` import if `pytest` becomes unused in the file; keep `test_library_only_configs_are_gated_to_macos` unchanged.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_macos_only_configs.py -v`
Expected: FAIL on `test_no_split_macos_files_remain` — 120 `-macos.toml` files exist.

- [ ] **Step 3: Merge the pairs**

Run this one-off script from the repository root:

```bash
uv run python - <<'PY'
import pathlib, tomllib

apps = pathlib.Path("src/mackup_ng/applications")
merged = 0
for mac in sorted(apps.glob("*-macos.toml")):
    base = apps / f"{mac.stem[: -len('-macos')]}.toml"
    assert base.exists(), base
    mac_data = tomllib.load(mac.open("rb"))
    base_data = tomllib.load(base.open("rb"))
    mac_files = [p for p in mac_data.get("files", []) if isinstance(p, str)]
    base_files = [p for p in base_data.get("files", []) if isinstance(p, str)]
    assert mac_files and base_files, (mac, base)
    assert all(p.startswith("Library/") for p in mac_files), mac
    assert not any(p.startswith("Library/") for p in base_files), base
    name = base_data.get("name", base.stem)

    def block(paths, when):
        body = "[[block]]\nfiles = [\n"
        body += "".join(f'    "{p}",\n' for p in paths)
        body += "]\n[block.when]\n" + when + "\n"
        return body

    base.write_text(
        f'name = "{name}"\n\n'
        + block(base_files, 'not_os = "macos"')
        + "\n"
        + block(mac_files, 'os = "macos"'),
    )
    mac.unlink()
    merged += 1
print("merged:", merged)
PY
```

- [ ] **Step 4: Verify the result**

Run: `uv run pytest tests/test_macos_only_configs.py -v`
Expected: PASS, 3 tests.

Inspect one result:

Run: `cat src/mackup_ng/applications/appcode-31.toml`
Expected:

```toml
name = "AppCode 3.1"

[[block]]
files = [
    "${MACKUP_XDG_CONFIG}/appCode31",
]
[block.when]
not_os = "macos"

[[block]]
files = [
    "Library/Preferences/appCode31",
]
[block.when]
os = "macos"
```

Confirm nothing leaks on this machine:

```bash
uv run python -c "
from mackup_ng.appsdb import ApplicationsDatabase
db = ApplicationsDatabase()
names = db.get_app_names()
leaks = [(a, p) for a in names if db.config_enabled(a) for p in db.get_files(a) if p.startswith('Library/')]
print('configs:', len(names), '| enabled:', sum(1 for a in names if db.config_enabled(a)))
print('library leaks:', len(leaks), leaks[:3])
"
```

Expected: `library leaks: 0 []`, and the config count drops by 120 from 744 to 624.

Run: `uv run pytest` and `make check`
Expected: green.

- [ ] **Step 5: Commit**

```bash
git add -A src/mackup_ng/applications tests/test_macos_only_configs.py
git commit -m "refactor(apps): fold the split macOS configs into gated blocks

The <app>-macos.toml convention doubled the application ids and scattered
one application across two files. Now each config carries two blocks, one
gated not_os = \"macos\" and one gated os = \"macos\".

not_os rather than os = [\"linux\", \"windows\"] so an android machine keeps
its XDG paths."
```

---

### Task 9: Documentation

**Files:**
- Modify: `README.md` (the Fork Additions section and the block description near line 1085), `AGENTS.md` (the action-blocks section), `doc/README.md`, `doc/ARCHITECTURE.md`

**Interfaces:**
- Consumes: the finished behaviour of Tasks 1-8.
- Produces: no code.

- [ ] **Step 1: Document the model**

In `README.md` and `AGENTS.md`, wherever action blocks are described, state the new model:

- A config is an ordered sequence of units: `pre` blocks, the top-level unit, `during` blocks, `post` blocks.
- `phase` is `"pre"` / `"during"` / `"post"`, default `"during"` — **changed from `"post"`**.
- A block may carry `files`, an action, or both. Within a unit, files sync first, then the action runs.
- Blocks take `files` only; `mapped_files` belongs to the top level.
- `[when]` on a block gates both its files and its action.

Update the env-contract and condition lists to include `not_os` alongside `os`, `not_marker` and `not_exists`.

- [ ] **Step 2: Document the macOS convention**

Replace any text describing `<app>-macos.toml` with the block form, using `appcode-31.toml` from Task 8 Step 4 as the worked example.

- [ ] **Step 3: Verify no stale references**

Run:

```bash
grep -rn 'phase = "post"\|-macos\.toml\|\[\[sync\]\]' --include='*.md' . \
  --exclude-dir=.venv --exclude-dir=.git --exclude-dir=dist --exclude-dir=docs
```

Expected: no output. `docs/superpowers/` is excluded because its specs and plans legitimately record the old shape.

- [ ] **Step 4: Run the gate**

Run: `make check`
Expected: green — `rumdl` lints the Markdown, so fix what it reports.

- [ ] **Step 5: Commit**

```bash
git add README.md AGENTS.md doc
git commit -m "docs: blocks as units of work"
```

---

## Self-Review

**Spec coverage.** The model and slot numbering → Tasks 2 and 5. `not_os` → Task 1. Config syntax → Tasks 2 and 8. `mapping.Pair.owner_slot` and `group_owners` → Task 3. The `ApplicationsDatabase` API table → Task 2. The sync loop → Task 5. `restart_service` across a config → Task 4. `mackup apply` → Task 6. `mackup show` → Task 7. Compatibility (default phase change) → covered by Task 5's `test_a_pre_block_runs_before_the_top_level_files` and Task 6's failing-first step. Migration of the 120 configs → Task 8. Error handling table → Task 2 (unknown phase, non-list `files`, unrecognized `[when]` keys). Documentation → Task 9. No spec section is unimplemented.

**Type consistency.** `Unit` carries `slot`, `when`, `passed`, `mappings`, `block` in Tasks 2, 5, 6 and 7 alike. `get_file_mappings` returns `(local, backup, slot)` in Task 2 and is consumed with that arity in Task 3. `group_owners` returns `dict[str, tuple[str, int]]` in Task 3 and is consumed as `groups_by_slot` keyed by `(app, slot)` in Tasks 3 and 5. `apply_unit_action(block, env_files, dry_run, pending_starts)` and `flush_pending_starts(pending_starts)` keep their signatures across Tasks 4, 5 and 6.

**Ordering constraint.** Task 3 adds a `groups_by_owner` compatibility shim so its own commit stays green; Task 5 deletes it. Running Task 5 before Task 3 leaves `groups_by_slot` undefined.

**Known risk.** Task 5 is the one that can fail quietly: a mistake in the slot key produces files synced in the wrong order, or not at all, without an exception. `tests/test_unit_order.py` asserts on observable effects — a file present, a marker created by a script that checked for it — rather than on call order, so a silently skipped sync fails the test.
