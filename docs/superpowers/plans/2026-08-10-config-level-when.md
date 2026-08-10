# Config-Level `[when]` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a top-level `[when]` table gate a whole config — its `files` and `[mapped_files]` as well as its blocks — so a machine-specific mapping can decline to claim its destination.

**Architecture:** `conditions.py` gains a config-level entry point over the same evaluation it already does for blocks. `appsdb.py` reserves the `when` key (so it stops leaking into the implicit top-level block), records each config's condition table, and answers `config_enabled(name)`. `main.py` consults that answer everywhere a config is consumed: building the sync plan, running blocks during `sync`, and `apply`. Reporting comes last, in its own task.

**Tech Stack:** Python 3.12+, stdlib only (`tomllib`, `os`, `platform`, `shutil`), `docopt-ng` for the CLI, `pytest` running `unittest.TestCase` classes.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-10-config-level-when-design.md`. Read it before Task 1.
- No new condition keys. `conditions._CONDITION_KEYS` stays as it is: `os`, `arch`, `marker`, `not_marker`, `command`, `gui`, `exists`, `not_exists`, `env`.
- A gated-out config contributes **nothing**: no sync pairs, no blocks — neither the implicit top-level block nor any entry of its `[[block]]` array.
- `when` becomes a reserved top-level key alongside `name`, `files`, `configuration_files`, `mapped_files`, `source_env`, `block`, `application`.
- `mackup list` still lists a gated-out config; `mackup show` still describes it.
- Python floor is `requires-python >=3.12`; tests are `unittest.TestCase` classes run by pytest.
- Toolchain: `uv` is NOT on the default PATH — run `export PATH="$HOME/.hermes/bin:$PATH"` first in every shell command that uses uv. Full suite `uv run pytest -q` (165 tests pass at the branch point and must stay green), `uv run ruff check src tests`, `uv run mypy src`, full gate `make check`. If `uv run` regenerates `uv.lock`, revert it with `git checkout -- uv.lock`.
- Conventional Commits, imperative mood. No Co-Authored-By trailers.

---

## File Structure

**Create:**

- `tests/test_config_conditions.py` — end-to-end CLI tests for the gate: marker-driven override, blocks skipped, orphan reporting.

**Modify:**

- `src/mackup_ng/conditions.py` — add `config_passes(data)` next to `block_passes(block)`; one docstring's worth of new surface, no new evaluation logic.
- `src/mackup_ng/appsdb.py` — reserve `when`, record `app_conditions`, add `get_conditions()` / `config_enabled()`.
- `src/mackup_ng/main.py` — gate `build_sync_plan`, the `sync` per-config loop and the `apply` loop; then report the gate in `show` and under `sync -v`.
- `tests/test_conditions.py`, `tests/test_appsdb_blocks.py` — unit coverage for the new entry point and the reserved key.
- `AGENTS.md`, `README.md`, `doc/ARCHITECTURE.md` — document the semantics and the migration note.

The user's own configs (`~/.mackup/applications/termux.toml`, `76-termux-colors.toml`, `77-termux-colors-eink.toml`) live outside the repository. Task 5 rewrites them, and it is the only task that touches files outside the repo.

---

### Task 1: Config-level condition evaluation

**Files:**
- Modify: `src/mackup_ng/conditions.py:65-68`, `src/mackup_ng/appsdb.py:12-14` (imports), `:381-383` (containers), `:415-428` (reserved keys / implicit block), `:550-560` (getters)
- Test: `tests/test_conditions.py`, `tests/test_appsdb_blocks.py`

**Interfaces:**
- Consumes: `conditions.block_passes(block)` (existing), `hooks.os_kind()`, `hooks.has_marker()`.
- Produces:
  - `conditions.config_passes(data: dict) -> bool`
  - `ApplicationsDatabase.get_conditions(name: str) -> dict`
  - `ApplicationsDatabase.config_enabled(name: str) -> bool`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_conditions.py`:

```python
class TestConfigConditions(unittest.TestCase):
    def test_config_without_when_passes(self):
        assert conditions.config_passes({}) is True
        assert conditions.config_passes({"name": "x", "files": [".rc"]}) is True

    def test_config_when_is_evaluated_like_a_block(self):
        with patch("mackup_ng.hooks.os_kind", return_value="linux"):
            assert conditions.config_passes({"when": {"os": ["linux"]}})
            assert not conditions.config_passes({"when": {"os": ["android"]}})

    def test_config_marker_condition(self):
        with patch("mackup_ng.hooks.has_marker", side_effect=lambda n: n == "eink"):
            assert conditions.config_passes({"when": {"marker": ["eink"]}})
            assert not conditions.config_passes({"when": {"marker": ["nope"]}})
```

Append to `tests/test_appsdb_blocks.py` (it already has a fixture that writes a config into a temp `~/.mackup/applications` and builds an `ApplicationsDatabase`; reuse that class's `setUp` and `_write_app` helper — if the helper has a different name in that file, use whatever it is called there):

```python
class TestConfigLevelWhen(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="mackup_cfgwhen_home_")
        self._orig_home = os.environ.get("HOME")
        self._orig_xdg = os.environ.get("XDG_CONFIG_HOME")
        os.environ["HOME"] = self.home
        os.environ["XDG_CONFIG_HOME"] = os.path.join(self.home, ".config")
        self.apps_dir = os.path.join(self.home, ".mackup", "applications")
        os.makedirs(self.apps_dir, exist_ok=True)

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

    def _write_app(self, name, body):
        with open(os.path.join(self.apps_dir, f"{name}.toml"), "w") as handle:
            handle.write(f'name = "{name}"\n{body}')

    def test_conditions_are_recorded_and_kept_out_of_the_implicit_block(self):
        self._write_app(
            "gated",
            'files = [".gatedrc"]\n\n'
            '[when]\nos = ["android"]\n\n'
            '[chmod]\npath = "~/.gatedrc"\nmode = "600"\n',
        )
        db = ApplicationsDatabase()
        assert db.get_conditions("gated") == {"os": ["android"]}
        cfg_blocks = db.get_blocks("gated")
        assert len(cfg_blocks) == 1
        assert "when" not in cfg_blocks[0]
        assert "chmod" in cfg_blocks[0]

    def test_config_enabled_follows_the_conditions(self):
        self._write_app("gated", 'files = [".gatedrc"]\n\n[when]\nos = ["android"]\n')
        self._write_app("plain", 'files = [".plainrc"]\n')
        db = ApplicationsDatabase()
        with patch("mackup_ng.hooks.os_kind", return_value="linux"):
            assert db.config_enabled("gated") is False
            assert db.config_enabled("plain") is True
        with patch("mackup_ng.hooks.os_kind", return_value="android"):
            assert db.config_enabled("gated") is True
```

Make sure `tests/test_appsdb_blocks.py` imports what these tests need: `os`, `shutil`, `tempfile`, `unittest`, `from unittest.mock import patch`, and `from mackup_ng.appsdb import ApplicationsDatabase`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_conditions.py tests/test_appsdb_blocks.py -v`
Expected: FAIL — `AttributeError: module 'mackup_ng.conditions' has no attribute 'config_passes'` and `AttributeError: 'ApplicationsDatabase' object has no attribute 'get_conditions'`.

- [ ] **Step 3: Add the config-level entry point**

Append to `src/mackup_ng/conditions.py`:

```python
def config_passes(data: dict) -> bool:
    """True iff every condition in a config's top-level ``[when]`` passes.

    A config's conditions gate everything it declares — its sync entries as
    well as its blocks — and use the same vocabulary as a block's ``[when]``.
    """
    return block_passes(data)
```

- [ ] **Step 4: Reserve `when` and record it in appsdb**

In `src/mackup_ng/appsdb.py`, extend the import line:

```python
from . import blocks, conditions, constants, utils
```

Add the container next to the others in `__init__`:

```python
        self.app_conditions: dict[str, dict] = {}
```

Record it right after `self.app_order.append(app_name)`:

```python
            when = data.get("when")
            self.app_conditions[app_name] = dict(when) if isinstance(when, dict) else {}
```

Add `"when"` to the `reserved` set so it stops becoming part of the implicit
top-level block:

```python
            reserved = {
                "name",
                "files",
                "configuration_files",
                "mapped_files",
                "source_env",
                "when",
                "block",
                "application",
            }
```

Add the getters next to `get_blocks`:

```python
    def get_conditions(self, name: str) -> dict:
        """Return the config's top-level ``[when]`` table (empty when absent)."""
        return dict(self.app_conditions.get(name, {}))

    def config_enabled(self, name: str) -> bool:
        """True when the config's conditions hold on this machine.

        A config that is not enabled declares nothing: no sync pairs and no
        blocks.
        """
        return conditions.config_passes({"when": self.app_conditions.get(name, {})})
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_conditions.py tests/test_appsdb_blocks.py -v`
Expected: PASS

- [ ] **Step 6: Run the full suite, lint, and commit**

```bash
uv run pytest -q
uv run ruff check src tests
uv run mypy src
git checkout -- uv.lock 2>/dev/null || true
git add src/mackup_ng/conditions.py src/mackup_ng/appsdb.py tests/test_conditions.py tests/test_appsdb_blocks.py
git commit -m "feat: record and evaluate config-level [when] conditions"
```

Note: the full suite must still show 165 passing plus the new tests. A pre-existing test that asserts the implicit block carries a `when` key would now fail — if one exists, update it to assert the config's conditions instead, and say so in the report.

---

### Task 2: Gate the plan, the sync loop and `apply`

**Files:**
- Modify: `src/mackup_ng/main.py:120-131` (`build_sync_plan`), `:379-398` (the `sync` per-config loop), `:454-465` (the `apply` loop)
- Test: `tests/test_config_conditions.py`

**Interfaces:**
- Consumes: `ApplicationsDatabase.config_enabled(name)` (Task 1), `mapping.build_pairs`, `mapping.group_by_source`, `mapping.group_owners` (existing).
- Produces: no new API — behavior only. `build_sync_plan(app_db, apps_to_sync)` keeps its signature `-> tuple[list[mapping.Pair], list[mapping.Eviction]]` and now skips configs whose conditions fail.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_config_conditions.py`:

```python
"""A config's top-level [when] gates its sync entries and its blocks."""

import io
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from mackup_ng import utils
from mackup_ng.main import main


class TestConfigLevelConditions(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="mackup_when_home_")
        self.storage = tempfile.mkdtemp(prefix="mackup_when_storage_")
        self.mackup_folder = os.path.join(self.storage, "Mackup")
        os.makedirs(self.mackup_folder, exist_ok=True)
        self._orig = {
            key: os.environ.get(key)
            for key in ("HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME")
        }
        os.environ["HOME"] = self.home
        os.environ["XDG_CONFIG_HOME"] = os.path.join(self.home, ".config")
        os.environ["XDG_STATE_HOME"] = os.path.join(self.home, ".local", "state")

        with open(os.path.join(self.home, ".mackup.cfg"), "w") as handle:
            handle.write(
                "[storage]\nengine = file_system\n"
                f"path = {self.storage}\ndirectory = Mackup\n\n"
                "[applications_to_sync]\naaa-base\nzzz-override\ngated-blocks\n",
            )
        self.apps_dir = os.path.join(self.home, ".mackup", "applications")
        os.makedirs(self.apps_dir, exist_ok=True)
        utils.FORCE_YES = True

    def tearDown(self):
        for key, orig in self._orig.items():
            if orig is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = orig
        shutil.rmtree(self.home, ignore_errors=True)
        shutil.rmtree(self.storage, ignore_errors=True)
        utils.FORCE_YES = False

    def _write_app(self, name, body):
        with open(os.path.join(self.apps_dir, f"{name}.toml"), "w") as handle:
            handle.write(f'name = "{name}"\n{body}')

    def _write_backup(self, relative, content):
        path = os.path.join(self.mackup_folder, relative)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as handle:
            handle.write(content)

    def _set_marker(self, name):
        markers = os.path.join(
            os.environ["XDG_STATE_HOME"], "mackup", "markers",
        )
        os.makedirs(markers, exist_ok=True)
        open(os.path.join(markers, name), "a").close()

    def _write_palette_configs(self):
        self._write_app(
            "aaa-base",
            '[mapped_files]\n".colors" = ".palette-default"\n',
        )
        self._write_app(
            "zzz-override",
            '[when]\nmarker = ["eink"]\n\n'
            '[mapped_files]\n".colors" = ".palette-eink"\n',
        )
        self._write_backup(".palette-default", "default\n")
        self._write_backup(".palette-eink", "eink\n")

    def test_override_is_inactive_without_the_marker(self):
        self._write_palette_configs()
        with patch("sys.argv", ["mackup", "sync"]):
            main()
        with open(os.path.join(self.home, ".colors")) as handle:
            assert handle.read() == "default\n"

    def test_override_wins_when_the_marker_is_set(self):
        self._write_palette_configs()
        self._set_marker("eink")
        with patch("sys.argv", ["mackup", "sync"]):
            main()
        with open(os.path.join(self.home, ".colors")) as handle:
            assert handle.read() == "eink\n"

    def test_gated_out_config_runs_no_blocks(self):
        touched = os.path.join(self.home, "block-ran.txt")
        self._write_app(
            "gated-blocks",
            '[when]\nmarker = ["nope"]\n\n'
            f'[run]\nscript = "touch {touched}"\n\n'
            '[[block]]\n'
            f'[block.run]\nscript = "touch {touched}.two"\n',
        )
        with patch("sys.argv", ["mackup", "sync"]):
            main()
        assert not os.path.exists(touched)
        assert not os.path.exists(f"{touched}.two")

    def test_enabled_config_still_runs_its_blocks(self):
        touched = os.path.join(self.home, "block-ran.txt")
        self._write_app(
            "gated-blocks",
            '[when]\nnot_marker = ["nope"]\n\n'
            f'[run]\nscript = "touch {touched}"\n',
        )
        with patch("sys.argv", ["mackup", "sync"]):
            main()
        assert os.path.exists(touched)

    def test_apply_skips_a_gated_out_config(self):
        touched = os.path.join(self.home, "applied.txt")
        self._write_app(
            "gated-blocks",
            '[when]\nmarker = ["nope"]\n\n'
            f'[run]\nscript = "touch {touched}"\n',
        )
        with patch("sys.argv", ["mackup", "apply"]):
            main()
        assert not os.path.exists(touched)

    def test_source_orphaned_by_a_gated_out_config_is_untouched(self):
        self._write_palette_configs()
        self._write_app(
            "zzz-override",
            '[when]\nmarker = ["eink"]\n\n'
            '[mapped_files]\n".colors" = ".palette-eink"\n',
        )
        with patch("sys.argv", ["mackup", "sync"]):
            main()
        with open(os.path.join(self.mackup_folder, ".palette-eink")) as handle:
            assert handle.read() == "eink\n"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_config_conditions.py -v`
Expected: FAIL — `test_override_is_inactive_without_the_marker` finds `eink` in `~/.colors` (the override still claims the destination) and the two block tests find the touched files.

- [ ] **Step 3: Gate the plan builder**

In `src/mackup_ng/main.py`, add the condition to `build_sync_plan`:

```python
def build_sync_plan(
    app_db: ApplicationsDatabase,
    apps_to_sync: set[str],
) -> tuple[list[mapping.Pair], list[mapping.Eviction]]:
    """Collect every selected app's pairs in read order and resolve them.

    A config whose top-level ``[when]`` does not hold on this machine declares
    nothing, so it never claims — or evicts — a destination.
    """
    entries = [
        mapping.Pair(source=backup, dest=local, owner_app=app_name)
        for app_name in app_db.get_app_order()
        if app_name in apps_to_sync
        and app_db.app_has_sync(app_name)
        and app_db.config_enabled(app_name)
        for local, backup in app_db.get_file_mappings(app_name)
    ]
    return mapping.build_pairs(entries)
```

- [ ] **Step 4: Gate the sync loop and `apply`**

In the `sync` branch's per-config loop, skip a gated-out config before anything
else happens for it:

```python
        for app_name in sorted(app_db.get_app_names()):
            if not app_db.config_enabled(app_name):
                continue
            env_files = app_db.get_env_files(app_name)
```

In the `apply` branch, the same:

```python
        for app_name in sorted(app_db.get_app_names()):
            if not app_db.config_enabled(app_name):
                continue
            env_files = app_db.get_env_files(app_name)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_config_conditions.py -v`
Expected: PASS (6 tests)

- [ ] **Step 6: Run the full suite, lint, and commit**

```bash
uv run pytest -q
uv run ruff check src tests
uv run mypy src
git checkout -- uv.lock 2>/dev/null || true
git add src/mackup_ng/main.py tests/test_config_conditions.py
git commit -m "feat: gate a config's sync entries and blocks by its [when]"
```

---

### Task 3: Report the gate in `show` and under `sync -v`

**Files:**
- Modify: `src/mackup_ng/main.py:269-323` (`show`), `:379-381` (the `sync` loop's skip from Task 2)
- Test: `tests/test_config_conditions.py`

**Interfaces:**
- Consumes: `ApplicationsDatabase.config_enabled(name)`, `ApplicationsDatabase.get_conditions(name)` (Task 1).
- Produces: no new API — output only.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config_conditions.py` inside `TestConfigLevelConditions`:

```python
    def test_show_reports_unmet_conditions(self):
        self._write_palette_configs()
        buffer = io.StringIO()
        with patch("sys.stdout", buffer), patch(
            "sys.argv", ["mackup", "show", "zzz-override"],
        ):
            main()
        output = buffer.getvalue()
        assert "conditions not met on this machine" in output
        assert "marker" in output

    def test_show_says_nothing_about_conditions_when_they_hold(self):
        self._write_palette_configs()
        self._set_marker("eink")
        buffer = io.StringIO()
        with patch("sys.stdout", buffer), patch(
            "sys.argv", ["mackup", "show", "zzz-override"],
        ):
            main()
        assert "conditions not met" not in buffer.getvalue()

    def test_verbose_sync_reports_the_skipped_config(self):
        self._write_palette_configs()
        buffer = io.StringIO()
        with patch("sys.stdout", buffer), patch(
            "sys.argv", ["mackup", "-v", "sync"],
        ):
            main()
        assert "zzz-override: conditions not met on this machine" in buffer.getvalue()

    def test_non_verbose_sync_stays_quiet_about_it(self):
        self._write_palette_configs()
        buffer = io.StringIO()
        with patch("sys.stdout", buffer), patch("sys.argv", ["mackup", "sync"]):
            main()
        assert "conditions not met" not in buffer.getvalue()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_config_conditions.py -k "reports_unmet or verbose_sync_reports or stays_quiet or says_nothing" -v`
Expected: FAIL with `AssertionError` — nothing prints the phrase yet.

- [ ] **Step 3: Report it in `show`**

In the `show` branch, right after the `Name:` line is printed:

```python
        print(f"{bold('Name:')} {pretty}")
        if not app_db.config_enabled(requested_app_name):
            unmet = ", ".join(
                f"{key}={value}"
                for key, value in sorted(app_db.get_conditions(requested_app_name).items())
            )
            print(
                utils.style_text(
                    f"conditions not met on this machine ({unmet})",
                    color=utils.AnsiColor.GRAY,
                ),
            )
```

- [ ] **Step 4: Report it under `sync -v`**

Replace the bare `continue` added in Task 2's sync loop with:

```python
            if not app_db.config_enabled(app_name):
                if verbose:
                    print(
                        utils.colorize_message(
                            f"{app_name}: conditions not met on this machine",
                        ),
                    )
                continue
```

Leave the `apply` loop's skip silent — `apply` already prints only configs that
did something.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_config_conditions.py -v`
Expected: PASS (10 tests)

- [ ] **Step 6: Run the full suite, lint, and commit**

```bash
uv run pytest -q
uv run ruff check src tests
uv run mypy src
git checkout -- uv.lock 2>/dev/null || true
git add src/mackup_ng/main.py tests/test_config_conditions.py
git commit -m "feat: report configs skipped by their conditions in show and sync -v"
```

---

### Task 4: Document the semantics and the migration

**Files:**
- Modify: `AGENTS.md`, `README.md`, `doc/ARCHITECTURE.md`
- Test: `make check` (rumdl lints the docs)

**Interfaces:**
- Consumes: everything from Tasks 1–3.
- Produces: no API.

- [ ] **Step 1: Document it in `AGENTS.md`**

`AGENTS.md` describes action blocks and their `[when]` sub-table. Add a
subsection after that description:

```markdown
### Config-level conditions (top-level `[when]`)

A top-level `[when]` table is the **config's** condition, evaluated with the
same keys as a block's `[when]` (`os`, `arch`, `marker`, `not_marker`,
`command`, `gui`, `exists`, `not_exists`, `env`). When it does not hold on
this machine the config contributes nothing: no `files`, no `[mapped_files]`,
and no blocks — not the implicit top-level block, not any `[[block]]` entry.

That is what lets a machine-specific mapping decline to claim its
destination, so an earlier config keeps it:

```toml
# zz-termux-colors-eink.toml — only on e-ink machines
[when]
os = ["android"]
marker = ["eink"]

[mapped_files]
".termux/colors.properties" = ".termux/colors-eink.properties"
```

To gate a single action rather than the whole config, put the condition in
the block instead:

```toml
[[block]]
[block.when]
os = ["linux"]
[block.chmod]
path = "~/.ssh"
```

**Migration:** a config that combined a top-level `[when]` with `files` used
to sync those files everywhere and gate only its action. Now the file list is
conditional too. Move the condition into `[[block]]` + `[block.when]` if the
old behavior was what you wanted.
```

- [ ] **Step 2: Document it in `README.md`**

Add the same rule in the README's config-format section, in the README's
voice, right after the action-block conditions are introduced:

```markdown
Conditions written at the top level of a config gate the **whole** config —
its synced files as well as its actions. A config whose conditions do not hold
on this machine declares nothing, so a mapping from another config keeps the
destination. That is how one machine can take a different source for the same
local file:

```toml
# ~/.mackup/applications/zz-termux-colors-eink.toml
[when]
os = ["android"]
marker = ["eink"]

[mapped_files]
".termux/colors.properties" = ".termux/colors-eink.properties"
```

Run `mackup show <app>` to see whether a config's conditions hold here, and
`mackup sync -v` to list the configs skipped for that reason. To gate a single
action instead of the config, put the condition inside `[[block]]` as
`[block.when]`.
```

- [ ] **Step 3: Document it in `doc/ARCHITECTURE.md`**

In the Sync Flow block, add the gate as the step before pair resolution:

```text
appsdb.py loads application definitions in precedence order
    ↓
configs whose top-level [when] does not hold are dropped — no pairs, no blocks
    ↓
mapping.py resolves (source, destination) pairs — later pairs win the
destination, sources group into fanout groups
```

- [ ] **Step 4: Run the docs lint and the full gate**

```bash
uv run rumdl check README.md AGENTS.md doc/ARCHITECTURE.md
make check
```
Expected: clean. (`make check`'s `rumdl check .` may report "No markdown files found to check." in some environments; the explicit file list above is the real check.)

- [ ] **Step 5: Commit**

```bash
git checkout -- uv.lock 2>/dev/null || true
git add AGENTS.md README.md doc/ARCHITECTURE.md
git commit -m "docs: describe config-level [when] and its migration"
```

---

### Task 5: Rewrite the user's Termux configs

**Files:**
- Modify: `~/.mackup/applications/termux.toml`
- Create: `~/.mackup/applications/zz-termux-colors-eink.toml`
- Delete: `~/.mackup/applications/76-termux-colors.toml`, `~/.mackup/applications/77-termux-colors-eink.toml`

These files are outside the repository — do not commit them. This task's
verification is a dry run, not a test file.

**Interfaces:**
- Consumes: the config-level `[when]` semantics from Tasks 1–3.
- Produces: nothing the repository depends on.

- [ ] **Step 1: Record the current resolution for comparison**

```bash
uv run mackup-ng -n -v sync 2>&1 | grep -E "termux|colors" | head -20
```
Keep the output; Step 4 compares against it.

- [ ] **Step 2: Rewrite `termux.toml`**

Replace `~/.mackup/applications/termux.toml` with:

```toml
name = "Configuration for Termux"

[when]
os = ["android"]

files = [
    ".termux/termux.properties",
    ".termux/font.ttf",
]

[mapped_files]
".termux/colors.properties" = ".termux/Neutral.properties"

# Make boot scripts executable after sync.
[[block]]
[block.chmod]
path = "~/.termux/boot"
recursive = true
file_mode = "+x"
```

Note what changed: `.termux/colors.properties` moved out of `files` into
`[mapped_files]` so it takes its content from a named palette; the chmod's own
`[when] os = ["android"]` is gone because the config-level `[when]` now covers
it; and the chmod moved into a `[[block]]` entry so the top level carries only
the config's condition.

- [ ] **Step 3: Create `zz-termux-colors-eink.toml` and delete the old pair**

Write `~/.mackup/applications/zz-termux-colors-eink.toml`:

```toml
name = "Termux colors (e-ink)"

# Sorts after termux.toml, so this mapping wins the destination on e-ink
# machines. Without the marker the config declares nothing and the base
# palette from termux.toml stays.
[when]
os = ["android"]
marker = ["eink"]

[mapped_files]
".termux/colors.properties" = ".termux/colors-eink.properties"
```

Then remove the superseded configs:

```bash
rm ~/.mackup/applications/76-termux-colors.toml
rm ~/.mackup/applications/77-termux-colors-eink.toml
```

- [ ] **Step 4: Verify with a dry run**

```bash
uv run mackup-ng -n -v sync 2>&1 | grep -E "termux|colors|evicted|no destination" | head -20
uv run mackup-ng show termux
uv run mackup-ng show zz-termux-colors-eink
```

Expected on this (Linux) machine: both configs report `conditions not met on
this machine`, no `.termux` pair appears in the plan, and
`.termux/Neutral.properties` / `.termux/colors-eink.properties` appear as
orphaned sources under `-v` — untouched, since nothing declares them here.
Nothing under `~/.termux` may be written or deleted by the dry run.

- [ ] **Step 5: Report what the backup folder still holds**

```bash
ls ~/Sync/Configs/Mackup/.termux/
```

The old shared `.termux/colors.properties` in the backup folder is now unused.
Report its presence to the user and let them decide whether to delete it — do
not delete anything under `~/Sync/Configs` yourself.

---

## Self-Review Notes

Spec coverage check:

- Config-level `[when]` semantics, reserved key, no new condition keys → Task 1.
- Gated-out config contributes no pairs and no blocks; `apply` honours the gate → Task 2.
- Orphaned source untouched; `show` and `sync -v` reporting; `list` unchanged → Tasks 2–3.
- Compatibility note and documentation → Task 4.
- Termux config rewrite, including the leftover backup file → Task 5.
