# Unified Config Blocks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Merge `applications` (sync lists) and `sets` (post-sync actions) into one TOML format under `~/.mackup/applications/`, where each file may declare a sync list, mapped files, and richly-conditioned action blocks.

**Architecture:** A single `[[block]]` array per config file carries `type` + `phase` + a uniform condition set. `appsdb` parses blocks alongside `[application]`; a new `blocks` executor (refactored from `sets.py`) applies them; the `mackup sync` loop runs each config's pre-blocks, syncs its files, then post-blocks. Condition evaluation is centralized in `conditions.py`.

**Tech Stack:** Python 3.11+ (`tomllib`), pytest, ruff.

## Global Constraints

- Python floor: 3.11 (`tomllib` in stdlib). Copied from `pyproject.toml`.
- Built-in path vars are `${MACKUP_XDG_CONFIG|DATA|STATE|CACHE}` (reserved); any other `${VAR}` resolves from env / `source_env`.
- Marker state lives in `$XDG_STATE_HOME/mackup/markers/`; definitions in `~/.mackup/markers/` + package `markers/`.
- Block condition list values are **any-of**; a block runs iff every condition on that block passes (no file-level inheritance — the top level of a config file is itself a block).
- `configuration_files` / `[mapped_files]` are sync declaration inside `[application]`, never blocks. A config's blocks are: the top-level implicit block (top-level action fields, if a `type` is present) followed by the `[[block]]` array entries.
- No change to the two-way-by-mtime sync engine, markers, or dconf.
- All new code passes `ruff check src/mackup_ng/ tests/` (config in `pyproject.toml`).
- Run tests with the project venv: `pytest tests/ -q`.

---

### Task 1: Condition evaluation (`conditions.py`)

Pure, dependency-light gate used by every block.

**Files:**
- Create: `src/mackup_ng/conditions.py`
- Test: `tests/test_conditions.py`

**Interfaces:**
- Consumes: `hooks.os_kind()`, `hooks.has_marker(name)`, `hooks._has_gui()` (exposed as `hooks.has_gui()` — see Step 3), `platform.machine()`.
- Produces:
  - `block_passes(block: dict) -> bool` — True iff every condition on `block` passes.
  - Condition keys handled: `require_os`, `require_arch`, `require_marker`, `skip_if_marker`, `require_command`, `require_gui`, `require_exists`, `skip_if_exists`, `require_env`.

- [ ] **Step 1: Expose `has_gui` publicly in hooks**

Rename the private helper so conditions can call it. In `src/mackup_ng/hooks.py` change `def _has_gui()` to `def has_gui()` and update its one caller in `hook_env` (`"MACKUP_HAS_GUI": _has_gui()` → `has_gui()`).

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_conditions.py
import os
import unittest
from unittest.mock import patch

from mackup_ng import conditions


class TestConditions(unittest.TestCase):
    def test_empty_block_passes(self):
        assert conditions.block_passes({}) is True

    def test_require_os_any_of(self):
        with patch("mackup_ng.hooks.os_kind", return_value="linux"):
            assert conditions.block_passes({"require_os": ["linux", "macos"]})
            assert not conditions.block_passes({"require_os": ["macos"]})

    def test_require_arch(self):
        with patch("mackup_ng.conditions.platform.machine", return_value="x86_64"):
            assert conditions.block_passes({"require_arch": ["x86_64"]})
            assert not conditions.block_passes({"require_arch": ["aarch64"]})

    def test_markers(self):
        with patch("mackup_ng.hooks.has_marker", side_effect=lambda n: n == "eink"):
            assert conditions.block_passes({"require_marker": ["eink"]})
            assert not conditions.block_passes({"require_marker": ["nope"]})
            assert not conditions.block_passes({"skip_if_marker": ["eink"]})
            assert conditions.block_passes({"skip_if_marker": ["nope"]})

    def test_require_command(self):
        with patch("mackup_ng.conditions.shutil.which", side_effect=lambda c: "/x" if c == "git" else None):
            assert conditions.block_passes({"require_command": ["git"]})
            assert not conditions.block_passes({"require_command": ["git", "nope"]})

    def test_require_gui(self):
        with patch("mackup_ng.hooks.has_gui", return_value=False):
            assert not conditions.block_passes({"require_gui": True})
            assert conditions.block_passes({"require_gui": False})

    def test_exists(self):
        assert conditions.block_passes({"require_exists": ["/"]})
        assert not conditions.block_passes({"require_exists": ["/no/such/path"]})
        assert conditions.block_passes({"skip_if_exists": ["/no/such/path"]})
        assert not conditions.block_passes({"skip_if_exists": ["/"]})

    def test_require_env_list_and_map(self):
        os.environ["COND_X"] = "yes"
        try:
            assert conditions.block_passes({"require_env": ["COND_X"]})
            assert conditions.block_passes({"require_env": {"COND_X": "yes"}})
            assert not conditions.block_passes({"require_env": {"COND_X": "no"}})
            assert not conditions.block_passes({"require_env": ["COND_MISSING"]})
        finally:
            os.environ.pop("COND_X", None)

    def test_multiple_conditions_all_apply(self):
        with patch("mackup_ng.hooks.os_kind", return_value="linux"), \
                patch("mackup_ng.conditions.platform.machine", return_value="x86_64"):
            assert conditions.block_passes(
                {"require_os": ["linux"], "require_arch": ["x86_64"]},
            )
            assert not conditions.block_passes(
                {"require_os": ["linux"], "require_arch": ["aarch64"]},
            )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_conditions.py -q`
Expected: FAIL (module `mackup_ng.conditions` not found).

- [ ] **Step 4: Implement `conditions.py`**

```python
# src/mackup_ng/conditions.py
"""Uniform condition evaluation for config-file action blocks.

A block runs only if every condition in the file-level defaults AND every
condition on the block itself passes. List values are any-of; missing keys are
vacuously true.
"""

from __future__ import annotations

import os
import platform
import shutil

from . import hooks


def _as_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    return [str(value)]


def _one(block: dict, key: str) -> bool:  # noqa: PLR0911 - flat condition switch
    value = block.get(key)
    if value is None:
        return True
    if key == "require_os":
        return hooks.os_kind() in _as_list(value)
    if key == "require_arch":
        return platform.machine() in _as_list(value)
    if key == "require_marker":
        return all(hooks.has_marker(n) for n in _as_list(value))
    if key == "skip_if_marker":
        return not any(hooks.has_marker(n) for n in _as_list(value))
    if key == "require_command":
        return all(shutil.which(c) is not None for c in _as_list(value))
    if key == "require_gui":
        return (not value) or hooks.has_gui()
    if key == "require_exists":
        return all(os.path.exists(os.path.expanduser(p)) for p in _as_list(value))
    if key == "skip_if_exists":
        return not any(os.path.exists(os.path.expanduser(p)) for p in _as_list(value))
    if key == "require_env":
        if isinstance(value, dict):
            return all(os.environ.get(k) == v for k, v in value.items())
        return all(os.environ.get(k) is not None for k in _as_list(value))
    return True


_CONDITION_KEYS = (
    "require_os",
    "require_arch",
    "require_marker",
    "skip_if_marker",
    "require_command",
    "require_gui",
    "require_exists",
    "skip_if_exists",
    "require_env",
)


def block_passes(block: dict) -> bool:
    """True iff every condition on the block passes."""
    return all(_one(block, key) for key in _CONDITION_KEYS)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_conditions.py -q`
Expected: PASS (9 tests). Then `ruff check src/mackup_ng/conditions.py`.

- [ ] **Step 6: Commit**

```bash
git add src/mackup_ng/conditions.py src/mackup_ng/hooks.py tests/test_conditions.py
git commit -m "feat: add uniform block condition evaluation"
```

---

### Task 2: Block executor (`blocks.py`, refactor from `sets.py`)

Move the block-type handlers and service control out of `sets.py` into `blocks.py`, driven by a unified `[[block]]` dict (with `type`) instead of typed arrays. Keep behavior identical.

**Files:**
- Create: `src/mackup_ng/blocks.py` (move handlers from `src/mackup_ng/sets.py`)
- Test: `tests/test_blocks.py` (port from `tests/test_sets.py`)

**Interfaces:**
- Consumes: `conditions.block_passes`, `hooks.hook_env`, existing helpers to be moved: `_expand_path`, `pending_copies`, `write_copy`, `_parse_mode`, `_chmod_targets`, `pending_chmods`, `apply_mutate`, `atomic_write`, `dropin_path`, `dropin_content`, service control (`svc_*`, `ServiceCtl`), `_run_scripts` logic.
- Produces:
  - `apply_block(block: dict, env_files: list[str], dry_run: bool) -> None` — dispatch on `block["type"]` in `{copy, chmod, run, mutate_xml, systemd_dropin}`; unknown type logs a warning and is skipped. Wraps the action in `restart_service` bracketing when `block.get("restart_service")` is set.
  - `apply_blocks(blocks: list[dict], phase: str, env_files: list[str], dry_run: bool) -> None` — iterate `blocks` in order; for each with `block.get("phase", "post") == phase` and `conditions.block_passes(block)`, call `apply_block`.

- [ ] **Step 1: Create `blocks.py` by moving the handler code**

Move these from `sets.py` into a new `src/mackup_ng/blocks.py` verbatim (they are already written and tested): `_msg`, `_expand_path`, `pending_copies`, `write_copy`, `_parse_mode`, `_chmod_targets`, `pending_chmods`, the XML helpers (`ensure_path`, `targets_for`, `apply_mutate`, `atomic_write`), value resolution (`_VAR_RE`, `_source_and_get`, `resolve_value`), service control (`_sysd`, `_brew`, `service_manager`, `svc_is_active`, `svc_stop`, `svc_start`, `ServiceCtl`, `_run_shell`), and drop-in helpers (`dropin_content`, `dropin_path`). Keep imports (`glob`, `os`, `re`, `shutil`, `subprocess`, `tempfile`, `xml.etree.ElementTree`, `dataclass`, `from . import hooks, utils`, `from . import conditions`).

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_blocks.py
import os
import tempfile
import unittest

from mackup_ng import blocks


class TestBlocks(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="mackup_blocks_")
        self._orig = {k: os.environ.get(k) for k in ("HOME", "XDG_STATE_HOME")}
        os.environ["HOME"] = self.home
        os.environ["XDG_STATE_HOME"] = os.path.join(self.home, ".local", "state")

    def tearDown(self):
        for k, v in self._orig.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_copy_block_applies(self):
        src = os.path.join(self.home, "s.txt")
        with open(src, "w") as f:
            f.write("hi")
        blocks.apply_block(
            {"type": "copy", "from": src, "to": "~/d.txt"}, [], dry_run=False,
        )
        assert open(os.path.join(self.home, "d.txt")).read() == "hi"

    def test_run_block_commands(self):
        state = os.path.join(self.home, "flag")
        blocks.apply_block(
            {"type": "run", "commands": [f'touch "{state}"']}, [], dry_run=False,
        )
        assert os.path.isfile(state)

    def test_unknown_type_skipped(self):
        # must not raise
        blocks.apply_block({"type": "bogus"}, [], dry_run=False)

    def test_apply_blocks_phase_and_order(self):
        a = os.path.join(self.home, "a")
        b = os.path.join(self.home, "b")
        blocks.apply_blocks(
            [
                {"type": "run", "phase": "pre", "commands": [f'echo x > "{a}"']},
                {"type": "run", "phase": "post", "commands": [f'echo x > "{b}"']},
            ],
            phase="pre",
            env_files=[],
            dry_run=False,
        )
        assert os.path.isfile(a)
        assert not os.path.exists(b)

    def test_apply_blocks_condition_gate(self):
        out = os.path.join(self.home, "gated")
        blocks.apply_blocks(
            [{"type": "run", "require_marker": ["nope"], "commands": [f'touch "{out}"']}],
            phase="post",
            env_files=[],
            dry_run=False,
        )
        assert not os.path.exists(out)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_blocks.py -q`
Expected: FAIL (`apply_block` / `apply_blocks` not defined).

- [ ] **Step 4: Implement `apply_block` and `apply_blocks`**

Append to `src/mackup_ng/blocks.py`:

```python
from dataclasses import dataclass  # already imported at top; shown for context

from . import conditions


def _apply_copy(block: dict, env_files: list[str], dry_run: bool) -> None:
    env = hooks.hook_env("restore")
    pending = pending_copies([block], env)
    for src, dst in pending:
        if dry_run:
            _msg(f"would copy {dst}")
            continue
        write_copy(src, dst)
        _msg(f"copied {dst}")


def _apply_chmod(block: dict, env_files: list[str], dry_run: bool) -> None:
    env = hooks.hook_env("restore")
    for path, mode in pending_chmods([block], env):
        if dry_run:
            _msg(f"would chmod {mode:o} {path}")
            continue
        os.chmod(path, mode)
        _msg(f"chmod {mode:o} {path}")


def _apply_run(block: dict, env_files: list[str], dry_run: bool) -> None:
    _run_scripts("block", [block], dry_run)  # reuse existing runner (require_command, commands/script)


def _apply_mutate_xml(block: dict, env_files: list[str], dry_run: bool) -> None:
    files = _resolve_files(block, env_files)  # move resolve_files() from sets.py too
    for path in files:
        try:
            tree = ET.parse(path)
        except ET.ParseError:
            continue
        root = tree.getroot()
        before = ET.tostring(root, encoding="utf-8")
        apply_mutate(root, block, env_files)
        if ET.tostring(root, encoding="utf-8") != before and not dry_run:
            atomic_write(path, tree)
            _msg(f"updated {path}")


def _apply_dropin(block: dict, env_files: list[str], dry_run: bool) -> None:
    target = dropin_path(block)
    content = dropin_content(block)
    old = ""
    if os.path.isfile(target):
        with open(target) as fh:
            old = fh.read()
    if old == content or dry_run:
        return
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w") as fh:
        fh.write(content)
    _msg(f"drop-in {target}")
    if service_manager() == "systemctl":
        _sysd("daemon-reload")


_HANDLERS = {
    "copy": _apply_copy,
    "chmod": _apply_chmod,
    "run": _apply_run,
    "mutate_xml": _apply_mutate_xml,
    "systemd_dropin": _apply_dropin,
}


def apply_block(block: dict, env_files: list[str], dry_run: bool) -> None:
    """Apply one block, dispatching on its `type`, bracketed by restart_service."""
    handler = _HANDLERS.get(block.get("type"))
    if handler is None:
        _msg(f"Warning: unknown block type {block.get('type')!r}, skip")
        return
    svc = block.get("restart_service")
    was_active = svc_is_active(svc) if svc else False
    if was_active and not dry_run:
        svc_stop(svc)
    try:
        handler(block, env_files, dry_run)
    finally:
        if was_active and not dry_run:
            svc_start(svc)


def apply_blocks(
    blocks: list[dict],
    phase: str,
    env_files: list[str],
    dry_run: bool,
) -> None:
    """Apply blocks matching `phase`, in order, gated by conditions."""
    for block in blocks:
        if block.get("phase", "post") != phase:
            continue
        if not conditions.block_passes(block):
            continue
        apply_block(block, env_files, dry_run)
```

Also move `resolve_files()` from `sets.py` into `blocks.py` (rename to `_resolve_files`) — it expands `files`/`files_mode` for `mutate_xml`. It currently lives near the top of `sets.py`; copy it verbatim and prefix with `_`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_blocks.py -q`
Expected: PASS (5 tests). Then `ruff check src/mackup_ng/blocks.py`.

- [ ] **Step 6: Commit**

```bash
git add src/mackup_ng/blocks.py tests/test_blocks.py
git commit -m "feat: block executor (apply_block/apply_blocks) with phase + conditions"
```

---

### Task 3: Parse blocks in `appsdb`

Extend the loader so each config exposes its blocks, file-level condition defaults, and `source_env`.

**Files:**
- Modify: `src/mackup_ng/appsdb.py` (`__init__` loop, new accessors)
- Test: `tests/test_appsdb_blocks.py`

**Interfaces:**
- Consumes: parsed TOML `data` per config file.
- Produces (new `ApplicationsDatabase` methods):
  - `get_blocks(app_name: str) -> list[dict]` — the config's blocks in order: the top-level implicit block first (if the top level has a `type`), then the `[[block]]` entries. Each dict has keys `type`, `phase`, condition keys, and type fields.
  - `get_env_files(app_name: str) -> list[str]` — `[application].source_env` (or top-level `source_env`), used for `${VAR}` in blocks.
  - `app_has_sync(app_name: str) -> bool` — True if the config declares any `configuration_files`/`mapped_files` (i.e. not block-only).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_appsdb_blocks.py
import os
import tempfile
import unittest

from mackup_ng.appsdb import ApplicationsDatabase


class TestAppsdbBlocks(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="mackup_adb_")
        self._orig = {k: os.environ.get(k) for k in ("HOME", "XDG_CONFIG_HOME")}
        os.environ["HOME"] = self.home
        os.environ["XDG_CONFIG_HOME"] = os.path.join(self.home, ".config")
        self.apps = os.path.join(self.home, ".mackup", "applications")
        os.makedirs(self.apps)

    def tearDown(self):
        for k, v in self._orig.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _write(self, name, body):
        with open(os.path.join(self.apps, f"{name}.toml"), "w") as f:
            f.write(body)

    def test_hybrid_top_level_block(self):
        # top level IS the chmod block; [application] is separate sync declaration
        self._write(
            "openssh",
            '[application]\nname = "SSH"\nconfiguration_files = [".ssh"]\n\n'
            'type = "chmod"\npath = "~/.ssh"\nmode = "700"\n',
        )
        db = ApplicationsDatabase()
        assert ".ssh" in db.get_files("openssh")
        blocks = db.get_blocks("openssh")
        assert len(blocks) == 1
        assert blocks[0]["type"] == "chmod"
        assert db.app_has_sync("openssh")

    def test_top_level_block_precedes_block_array(self):
        self._write(
            "multi",
            'type = "run"\ncommands = ["a"]\n\n'
            '[[block]]\ntype = "run"\ncommands = ["b"]\n',
        )
        blocks = ApplicationsDatabase().get_blocks("multi")
        assert [b["commands"] for b in blocks] == [["a"], ["b"]]

    def test_block_only_config(self):
        self._write(
            "10-linger",
            'type = "run"\nrequire_os = ["linux"]\nrequire_command = ["loginctl"]\n'
            'script = "loginctl enable-linger $(id -un)"\n',
        )
        db = ApplicationsDatabase()
        assert "10-linger" in db.get_app_names()
        assert not db.app_has_sync("10-linger")
        block = db.get_blocks("10-linger")[0]
        assert block["type"] == "run"
        assert block["require_os"] == ["linux"]

    def test_source_env(self):
        self._write(
            "ff",
            '[application]\nname = "FF"\nsource_env = ["~/e"]\n'
            'configuration_files = ["${MACKUP_XDG_CONFIG}/ff"]\n',
        )
        db = ApplicationsDatabase()
        assert db.get_env_files("ff") == ["~/e"]


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_appsdb_blocks.py -q`
Expected: FAIL (`get_blocks` not defined).

- [ ] **Step 3: Store blocks/conditions/env in `__init__`**

In `src/mackup_ng/appsdb.py`, add instance dicts in `__init__` (near `self.apps = {}`):

```python
self.app_blocks: dict[str, list[dict]] = {}
self.app_env_files: dict[str, list[str]] = {}
```

Inside the per-config loop, after `application = data.get("application")`, change the guard so **block-only files (no `[application]`) still load**, and build the block list (top-level implicit block first, then `[[block]]`):

```python
application = data.get("application")
if application is None:
    application = {}
elif not isinstance(application, dict):
    continue

filename = os.path.basename(config_file)
app_name = filename[: -len(".toml")]
self.apps[app_name] = {}
self.apps[app_name]["name"] = application.get("name", app_name)

# The whole top level is one block: any top-level key that is not a
# sync/meta table becomes the top-level implicit block, prepended to [[block]].
reserved = {"application", "mapped_files", "block", "source_env"}
top_block = {k: v for k, v in data.items() if k not in reserved}
blocks = list(data.get("block", []))
if top_block.get("type"):
    blocks.insert(0, top_block)
self.app_blocks[app_name] = blocks
self.app_env_files[app_name] = list(
    application.get("source_env", data.get("source_env", [])),
)
env_files = self.app_env_files[app_name]
```

Keep the existing `configuration_files` / `mapped_files` loops below unchanged (they read from `application`, now possibly `{}` → no files, which is correct for block-only configs).

- [ ] **Step 4: Add accessor methods**

```python
def get_blocks(self, name: str) -> list[dict]:
    return list(self.app_blocks.get(name, []))

def get_env_files(self, name: str) -> list[str]:
    return list(self.app_env_files.get(name, []))

def app_has_sync(self, name: str) -> bool:
    files = self.apps.get(name, {}).get("configuration_files")
    return bool(files)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_appsdb_blocks.py tests/test_appsdb_xdg.py tests/test_mapping.py -q`
Expected: PASS (existing appsdb tests must still pass — block-only guard change is additive). Then `ruff check src/mackup_ng/appsdb.py`.

- [ ] **Step 6: Commit**

```bash
git add src/mackup_ng/appsdb.py tests/test_appsdb_blocks.py
git commit -m "feat: parse [[block]], file conditions and source_env in appsdb"
```

---

### Task 4: Wire blocks into `mackup sync`

Per config: run pre-blocks, sync files, run post-blocks. Include block-only configs in the loop.

**Files:**
- Modify: `src/mackup_ng/main.py` (the `args["sync"]` branch, ~lines 239-272)
- Test: `tests/test_cli.py` (add block cases)

**Interfaces:**
- Consumes: `app_db.get_blocks`, `get_env_files`, `app_has_sync`; `blocks.apply_blocks`.
- Produces: unified sync loop; `sets.apply_dir` call removed.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_cli.py` (uses the existing `TestCLI` fixture that writes `custom_app_config`):

```python
def test_sync_runs_post_block_after_files(self):
    # config that syncs a file AND chmods it via a post block
    target = os.path.join(self.test_home, ".secretrc")
    with open(target, "w") as f:
        f.write("k\n")
    os.chmod(target, 0o644)
    with open(self.custom_app_config, "w") as f:
        f.write(
            '[application]\n'
            f'name = "{self.test_app_name}"\n'
            'configuration_files = [".secretrc"]\n'
            '[[block]]\n'
            'type = "chmod"\n'
            'path = "~/.secretrc"\n'
            'mode = "600"\n',
        )
    with patch("sys.argv", ["mackup", "sync"]):
        main()
    assert os.stat(target).st_mode & 0o777 == 0o600
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli.py::TestCLI::test_sync_runs_post_block_after_files -q`
Expected: FAIL (perms still 644 — blocks not applied).

- [ ] **Step 3: Rewrite the sync loop**

In `src/mackup_ng/main.py`, replace the app loop and the trailing `sets.apply_dir(...)` line. The current loop (see `main.py:253-272`) iterates `sorted(mckp.get_apps_to_backup())` and calls `app.sync_files()`. Extend it:

```python
from . import blocks  # add to imports; remove `sets` import

# ... inside args["sync"] branch, replacing the for-loop and the sets.apply_dir call:
for app_name in sorted(app_db.get_app_names()):
    env_files = app_db.get_env_files(app_name)
    cfg_blocks = app_db.get_blocks(app_name)

    blocks.apply_blocks(cfg_blocks, "pre", env_files, dry_run)

    if app_name in mckp.get_apps_to_backup() and app_db.app_has_sync(app_name):
        pretty_name = app_db.get_name(app_name)
        app = ApplicationProfile(
            mckp,
            app_db.get_file_mappings(app_name),
            dry_run,
            verbose,
        )
        print_app_header(app_name, pretty_name)
        stats = app.sync_files()
        print_app_result(stats, app_name, pretty_name)

    blocks.apply_blocks(cfg_blocks, "post", env_files, dry_run)

if role == "restore" and dconf_enabled:
    dconf.load_all(dry_run)
```

Notes: iterate **all** configs (not just `get_apps_to_backup()`) so block-only files run; only sync files for configs that are both selected (`get_apps_to_backup()` respects `.mackup.cfg` ignore/sync) and have a sync list. Remove the old `for app_name in sorted(mckp.get_apps_to_backup())` block and the `sets.apply_dir(sets.default_sets_dir(), dry_run)` line.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cli.py -q`
Expected: PASS (all CLI tests, including the new one).

- [ ] **Step 5: Commit**

```bash
git add src/mackup_ng/main.py tests/test_cli.py
git commit -m "feat: apply config blocks per-app during sync (pre -> sync -> post)"
```

---

### Task 5: `mackup apply` command (blocks-only)

Replace `apply-sets` with `apply`: run every config's pre+post blocks without syncing files.

**Files:**
- Modify: `src/mackup_ng/main.py` (docopt usage string, command branch)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `app_db.get_blocks/get_env_files`, `blocks.apply_blocks`.
- Produces: `mackup apply` CLI verb.

- [ ] **Step 1: Write the failing test**

```python
def test_apply_runs_blocks_without_sync(self):
    out = os.path.join(self.test_home, ".applied")
    with open(self.custom_app_config, "w") as f:
        f.write(
            '[[block]]\n'
            'type = "run"\n'
            f'commands = [\'touch "{out}"\']\n',
        )
    with patch("sys.argv", ["mackup", "apply"]):
        main()
    assert os.path.isfile(out)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli.py::TestCLI::test_apply_runs_blocks_without_sync -q`
Expected: FAIL (`apply` not a recognized command).

- [ ] **Step 3: Add the `apply` command**

In `main.py` docstring usage, replace the `apply-sets` line with:

```
  mackup-ng [options] apply
```

and update the "Modes of action" description line to:

```
 - mackup-ng apply: run every config's action blocks without syncing files.
```

Add the branch (near the old `apply-sets` handling):

```python
elif args["apply"]:
    mckp.check_for_usable_environment()
    for app_name in sorted(app_db.get_app_names()):
        ef = app_db.get_env_files(app_name)
        cfg_blocks = app_db.get_blocks(app_name)
        blocks.apply_blocks(cfg_blocks, "pre", ef, dry_run)
        blocks.apply_blocks(cfg_blocks, "post", ef, dry_run)
```

Remove the old `elif args["apply-sets"]:` branch.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cli.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mackup_ng/main.py tests/test_cli.py
git commit -m "feat: replace apply-sets with blocks-only 'mackup apply'"
```

---

### Task 6: `list`/`show` and constants cleanup

Hide block-only configs from `list`; show blocks in `show`; drop `SETS_DIRNAME`.

**Files:**
- Modify: `src/mackup_ng/main.py` (`list`, `show` branches)
- Modify: `src/mackup_ng/constants.py` (remove `SETS_DIRNAME`)
- Modify: `src/mackup_ng/mackup.py` if it references `SETS_DIRNAME` (grep first)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `app_db.app_has_sync`, `app_db.get_blocks`.

- [ ] **Step 1: Write the failing test**

```python
def test_list_hides_block_only(self):
    with open(os.path.join(self.custom_apps_dir, "hookonly.toml"), "w") as f:
        f.write('[[block]]\ntype = "run"\ncommands = ["true"]\n')
    from io import StringIO
    from contextlib import redirect_stdout
    buf = StringIO()
    with patch("sys.argv", ["mackup", "list"]), redirect_stdout(buf):
        main()
    assert "hookonly" not in buf.getvalue()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli.py::TestCLI::test_list_hides_block_only -q`
Expected: FAIL (block-only shown in list).

- [ ] **Step 3: Filter `list`, extend `show`, drop constant**

In `main.py` `list` branch, filter to configs that sync:

```python
for app_name in sorted(app_db.get_app_names()):
    if not app_db.app_has_sync(app_name):
        continue
    print(f" - {app_name}")
```
(Match the existing print format; only add the `app_has_sync` guard.)

In `show`, after printing configuration files, print blocks:

```python
cfg_blocks = app_db.get_blocks(requested_app_name)
if cfg_blocks:
    print("Action blocks:")
    for b in cfg_blocks:
        print(f" - {b.get('phase', 'post')}: {b.get('type')}")
```

In `constants.py`, delete the `SETS_DIRNAME` line. Run `grep -rn SETS_DIRNAME src/` and remove any remaining references (there should be none after Task 4 removed the `sets` import; `default_sets_dir` lived in `sets.py` which is deleted in Task 7).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cli.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mackup_ng/main.py src/mackup_ng/constants.py tests/test_cli.py
git commit -m "feat: hide block-only configs from list, show blocks, drop SETS_DIRNAME"
```

---

### Task 7: Delete `sets.py`, migrate `~/.mackup/sets/` data, update docs

Retire the old module and directory; convert the user's sets to the new format.

**Files:**
- Delete: `src/mackup_ng/sets.py`, `tests/test_sets.py`
- Modify: docs `AGENTS.md`, `README.md`, `doc/README.md`
- Data (user machine, not repo): convert `~/.mackup/sets/*.toml` → `~/.mackup/applications/*.toml`

- [ ] **Step 1: Confirm nothing imports `sets`**

Run: `grep -rn "import sets\|from .sets\| sets\." src/mackup_ng/`
Expected: no results (Tasks 2/4/5 moved everything to `blocks`). If any remain, repoint them to `blocks` before deleting.

- [ ] **Step 2: Delete the module and its tests**

```bash
git rm src/mackup_ng/sets.py tests/test_sets.py
```

(The block behaviors are covered by `tests/test_blocks.py`; verify nothing unique to `test_sets.py` is lost — port any missing case, e.g. `mutate_xml` idempotency, into `test_blocks.py` first.)

- [ ] **Step 3: Convert the user's sets to configs**

For each file in `~/.mackup/sets/`, produce an `~/.mackup/applications/` config using the `[[block]]` format. Fold app-specific ones into their app file where natural; keep cross-cutting as block-only. Example — `40-fix-perms.toml` becomes blocks split across the apps whose files they touch:

`~/.mackup/applications/openssh.toml`:
```toml
[application]
name = "SSH"
configuration_files = [".ssh"]

[[block]]
type = "chmod"
path = "~/.ssh"
recursive = true
dir_mode = "700"
file_mode = "600"
```

`~/.mackup/applications/10-enable-linger.toml` (block-only, unchanged intent):
```toml
require_os = ["linux"]
skip_if_marker = ["no-linger"]

[[block]]
type = "run"
require_command = ["loginctl"]
script = 'loginctl enable-linger "$(id -un)"'
```

Convert the remaining sets (`20-syncthing-apikey`, `30-syncthing-low-resource`, `50-apps-prebuilds`, `60-theme-switcher`, `76/77-termux-colors`) the same way — each `[[copy]]`/`[[chmod]]`/`[[run]]`/`[[mutate_xml]]`/`[[systemd_dropin]]` becomes a `[[block]]` with a `type` field, moving file-level `require_marker`/`require_os`/`skip_if_marker` to the top of the file (file-level defaults). Then `rmdir ~/.mackup/sets`.

- [ ] **Step 4: Verify migrated configs load and apply**

Run: `pip install -e . && mackup -n apply` (dry-run) and `mackup -n sync`.
Expected: the migrated blocks appear (e.g. "would chmod 600 …/.ssh/config"); no errors; `mackup list` still shows 614 apps and hides block-only files.

- [ ] **Step 5: Update docs**

In `AGENTS.md` and `README.md`, replace the separate "Config sets (`sets/`)" documentation with the unified block model: one `applications/*.toml` with optional `[application]` + `[[block]]` (type, phase, conditions), and the execution order (pre → sync → post per config; filename order across configs). Update the `~/.mackup/` layout diagrams to drop `sets/`. In `doc/README.md`, update any `apply-sets` mention to `apply`.

- [ ] **Step 6: Full suite + ruff + commit**

Run: `pytest tests/ -q && ruff check src/mackup_ng/ tests/`
Expected: all pass, no lint errors.

```bash
git add -A
git commit -m "refactor: retire sets.py/sets dir in favor of unified config blocks; docs"
```

---

## Self-Review

**Spec coverage:**
- Superset format; sync (`[application]`/`[mapped_files]`) separate from blocks → Tasks 2, 3.
- Top-level implicit block + `[[block]]` array, top-level first → Task 3.
- Uniform per-block conditions (os/arch/marker/command/gui/exists/env) → Task 1.
- `type` + `phase` on blocks → Tasks 2, 3.
- Execution model (pre → sync → post, per config, filename order) → Task 4.
- File-sync gated by name; blocks by their own conditions → Tasks 3, 4.
- `mackup apply` (blocks-only) → Task 5.
- `list` hides block-only, `show` prints blocks → Task 6.
- Migration (sets → applications, remove sets/) + docs → Task 7.
- Stock 607 untouched → verified in Task 4/6 (regression) and Task 3 (block-only guard is additive; stock files have no `type` at top level, so no implicit block).

**Placeholder scan:** none — every step has concrete code/commands.

**Type consistency:** `apply_blocks(blocks, phase, env_files, dry_run)` and `block_passes(block)` signatures are used identically in Tasks 1, 2, 4, 5. `get_blocks/get_env_files/app_has_sync` names match across Tasks 3–6.

**Note on ordering:** the sync loop iterates `sorted(app_db.get_app_names())` so cross-config block order follows filename sort, matching the spec; app-specific blocks live in their app file and run in that config's post phase, right after its files sync.
