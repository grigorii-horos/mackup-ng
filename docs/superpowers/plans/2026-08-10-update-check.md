# Update Check Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tell the user at the end of `mackup sync` when a newer mackup-ng is on PyPI, at most once a day, with the upgrade command for their install method.

**Architecture:** A new `update.py` keeps the network in exactly one function (`fetch_latest`) and everything else — version parsing, comparison, the cache file, the install-method guess — pure and offline-testable. `check()` orchestrates them: marker, cache, fetch, message. `main.py`'s `sync` branch calls it once, at the very end, and only when not doing a dry run.

**Tech Stack:** Python 3.12+, stdlib only (`urllib.request`, `json`, `os`, `re`, `time`), `pytest` running `unittest.TestCase` classes.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-10-update-check-design.md`. Read it before Task 1.
- **No new dependency.** The project's only runtime dependency is `docopt-ng`; the check uses `urllib.request` from the stdlib.
- The network is confined to `fetch_latest`. Every other function must be testable with no socket.
- No test may touch the network. Tests inject a fake fetch and point `XDG_CACHE_HOME` at a temp directory.
- Any failure — offline, timeout, non-200, malformed JSON, missing field, unwritable cache — is swallowed. The check never raises out of `check()` and never breaks a sync.
- Pre-releases are never announced: a version is a dot-separated run of integers, anything else is ignored.
- Cache: `$XDG_CACHE_HOME/mackup/update-check.json`, holding `{"checked_at": <epoch seconds>, "latest": "<version>"}`, TTL 24 hours.
- The opt-out is the marker `no-update-check`. With it set: no fetch, no cache read, no output.
- `--dry-run` performs no fetch and writes no cache file.
- Python floor `>=3.12`; tests are `unittest.TestCase` classes run by pytest.
- Toolchain: `uv` is NOT on the default PATH — run `export PATH="$HOME/.hermes/bin:$PATH"` first in every shell command that uses uv. Full suite `uv run pytest -q` (189 tests pass at the branch point and must stay green), `uv run ruff check src tests`, `uv run mypy src`, full gate `make check`. If `uv run` regenerates `uv.lock`, revert it with `git checkout -- uv.lock`.
- Conventional Commits, imperative mood. No Co-Authored-By trailers.

---

## File Structure

**Create:**

- `src/mackup_ng/update.py` — the whole feature: version parsing and comparison, the cache file, the install-method guess, the single network call, and the `check()` orchestration.
- `src/mackup_ng/markers/no-update-check.toml` — the marker definition, so the opt-out shows up in `mackup-ng markers`.
- `tests/test_update.py` — unit tests for every function, all offline.

**Modify:**

- `src/mackup_ng/main.py` — one call at the end of the `sync` branch.
- `tests/test_cli.py` — an end-to-end test that a sync prints the notice and that `--dry-run` neither fetches nor writes the cache.
- `README.md`, `AGENTS.md` — what the check does, the once-a-day cadence, the PyPI contact and what it discloses, and the marker that turns it off.

---

### Task 1: Version parsing, the install-method guess, and the cache

**Files:**
- Create: `src/mackup_ng/update.py`, `tests/test_update.py`
- Test: `tests/test_update.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `parse_version(text: str) -> tuple[int, ...] | None`
  - `is_newer(latest: str, current: str) -> bool`
  - `upgrade_command(executable: str) -> str | None`
  - `cache_path() -> str`
  - `read_cache(now: float) -> str | None`
  - `write_cache(latest: str, now: float) -> None`
  - `CACHE_TTL_SECONDS: int`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_update.py`:

```python
"""Tests for the PyPI update check. Nothing here touches the network."""

import json
import os
import shutil
import tempfile
import unittest

from mackup_ng import update


class TestParseVersion(unittest.TestCase):
    def test_numeric_versions_parse_to_tuples(self):
        assert update.parse_version("2.1.0") == (2, 1, 0)
        assert update.parse_version("10") == (10,)
        assert update.parse_version(" 2.1.0 ") == (2, 1, 0)

    def test_pre_releases_and_junk_are_rejected(self):
        for text in ("2.2.0rc1", "2.2.0.dev3", "2.2.0-1", "", "unknown", "v2.1.0"):
            assert update.parse_version(text) is None


class TestIsNewer(unittest.TestCase):
    def test_newer_version_wins(self):
        assert update.is_newer("2.2.0", "2.1.0")
        assert update.is_newer("2.1.1", "2.1.0")
        assert update.is_newer("2.1.0", "2.1")

    def test_equal_or_older_is_not_newer(self):
        assert not update.is_newer("2.1.0", "2.1.0")
        assert not update.is_newer("2.0.9", "2.1.0")

    def test_unparseable_sides_are_never_newer(self):
        assert not update.is_newer("2.2.0rc1", "2.1.0")
        assert not update.is_newer("2.2.0", "unknown")


class TestUpgradeCommand(unittest.TestCase):
    def test_snap_path(self):
        assert (
            update.upgrade_command("/snap/mackup-ng/12/bin/mackup-ng")
            == "sudo snap refresh mackup-ng"
        )

    def test_uv_tool_path(self):
        assert (
            update.upgrade_command(
                "/home/x/.local/share/uv/tools/mackup-ng/bin/mackup-ng",
            )
            == "uv tool upgrade mackup-ng"
        )

    def test_pipx_path(self):
        assert (
            update.upgrade_command("/home/x/.local/pipx/venvs/mackup-ng/bin/mackup-ng")
            == "pipx upgrade mackup-ng"
        )

    def test_anything_else_falls_back_to_pip(self):
        assert (
            update.upgrade_command("/usr/local/bin/mackup-ng")
            == "pip install --upgrade mackup-ng"
        )

    def test_missing_path_gives_no_command(self):
        assert update.upgrade_command("") is None


class TestCache(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="mackup_update_home_")
        self._orig = {key: os.environ.get(key) for key in ("HOME", "XDG_CACHE_HOME")}
        os.environ["HOME"] = self.home
        os.environ["XDG_CACHE_HOME"] = os.path.join(self.home, ".cache")

    def tearDown(self):
        for key, orig in self._orig.items():
            if orig is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = orig
        shutil.rmtree(self.home, ignore_errors=True)

    def test_absent_cache_reads_as_none(self):
        assert update.read_cache(1000.0) is None

    def test_written_value_reads_back_within_the_ttl(self):
        update.write_cache("2.2.0", 1000.0)
        assert update.read_cache(1000.0) == "2.2.0"
        assert update.read_cache(1000.0 + update.CACHE_TTL_SECONDS - 1) == "2.2.0"

    def test_expired_entry_reads_as_none(self):
        update.write_cache("2.2.0", 1000.0)
        assert update.read_cache(1000.0 + update.CACHE_TTL_SECONDS + 1) is None

    def test_malformed_cache_reads_as_none(self):
        path = update.cache_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as handle:
            handle.write("{not json")
        assert update.read_cache(1000.0) is None

    def test_cache_missing_keys_reads_as_none(self):
        path = update.cache_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as handle:
            json.dump({"latest": "2.2.0"}, handle)
        assert update.read_cache(1000.0) is None

    def test_unwritable_cache_directory_is_swallowed(self):
        os.makedirs(os.environ["XDG_CACHE_HOME"], exist_ok=True)
        blocker = os.path.join(os.environ["XDG_CACHE_HOME"], "mackup")
        with open(blocker, "w") as handle:  # a file where the directory belongs
            handle.write("in the way\n")
        update.write_cache("2.2.0", 1000.0)  # must not raise
        assert update.read_cache(1000.0) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_update.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mackup_ng.update'`

- [ ] **Step 3: Write the implementation**

Create `src/mackup_ng/update.py`:

```python
"""Check PyPI for a newer mackup-ng and tell the user once a day.

The network lives in :func:`fetch_latest` alone; everything else — version
parsing, the cache file, the install-method guess — is offline and testable
without a socket. Nothing here may raise into a sync: a failed check is a
silent no-op.
"""

from __future__ import annotations

import json
import os
import re

CACHE_TTL_SECONDS: int = 24 * 60 * 60

_NUMERIC_VERSION_RE = re.compile(r"^\d+(?:\.\d+)*$")


def parse_version(text: str) -> tuple[int, ...] | None:
    """Return a comparable tuple, or None for a pre-release or junk.

    Only a dot-separated run of integers counts. ``2.2.0rc1`` and
    ``2.2.0.dev3`` are pre-releases and deliberately unparseable, which is how
    they stay unannounced.
    """
    stripped = str(text).strip()
    if not _NUMERIC_VERSION_RE.match(stripped):
        return None
    return tuple(int(part) for part in stripped.split("."))


def is_newer(latest: str, current: str) -> bool:
    """True when ``latest`` is a strictly newer release than ``current``."""
    new = parse_version(latest)
    now = parse_version(current)
    if new is None or now is None:
        return False
    return new > now


def upgrade_command(executable: str) -> str | None:
    """Guess the upgrade command from the path of the running executable."""
    if not executable:
        return None
    path = os.path.realpath(executable)
    parts = path.split(os.sep)
    if "snap" in parts:
        return "sudo snap refresh mackup-ng"
    if f"{os.sep}uv{os.sep}tools{os.sep}" in path:
        return "uv tool upgrade mackup-ng"
    if "pipx" in parts:
        return "pipx upgrade mackup-ng"
    return "pip install --upgrade mackup-ng"


def cache_path() -> str:
    """Path of the update-check cache file under ``$XDG_CACHE_HOME``."""
    base = os.environ.get(
        "XDG_CACHE_HOME",
        os.path.join(os.environ["HOME"], ".cache"),
    )
    return os.path.join(base, "mackup", "update-check.json")


def read_cache(now: float) -> str | None:
    """Return the cached version while it is fresh, else None.

    An absent, unreadable or malformed cache file is simply no answer.
    """
    try:
        with open(cache_path()) as handle:
            data = json.load(handle)
        checked_at = float(data["checked_at"])
        latest = str(data["latest"])
    except (OSError, ValueError, TypeError, KeyError):
        return None
    if now - checked_at > CACHE_TTL_SECONDS:
        return None
    return latest


def write_cache(latest: str, now: float) -> None:
    """Store the fetched version. A cache we cannot write is not an error."""
    path = cache_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as handle:
            json.dump({"checked_at": now, "latest": latest}, handle)
    except OSError:
        return
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_update.py -v`
Expected: PASS (17 tests)

- [ ] **Step 5: Run the full suite, lint, and commit**

```bash
uv run pytest -q
uv run ruff check src tests
uv run mypy src
git checkout -- uv.lock 2>/dev/null || true
git add src/mackup_ng/update.py tests/test_update.py
git commit -m "feat: add version parsing, cache and install-method guess for the update check"
```

---

### Task 2: The fetch and the `check` orchestration

**Files:**
- Modify: `src/mackup_ng/update.py`
- Create: `src/mackup_ng/markers/no-update-check.toml`
- Test: `tests/test_update.py`

**Interfaces:**
- Consumes: `parse_version`, `is_newer`, `upgrade_command`, `read_cache`, `write_cache`, `CACHE_TTL_SECONDS` (Task 1); `hooks.has_marker(name) -> bool` (existing).
- Produces:
  - `PYPI_URL: str`
  - `fetch_latest(timeout: float = 2.0) -> str | None`
  - `check(current: str, *, fetch: Callable[[], str | None] | None = None, now: float | None = None, verbose: bool = False) -> str | None`

**Important:** `check`'s `fetch` parameter must default to `None` and resolve to `fetch_latest` **inside the body**, not as a default argument value. A default bound at definition time cannot be patched by a test that replaces `update.fetch_latest`, and Task 3's CLI test does exactly that.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_update.py`:

```python
class TestCheck(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="mackup_check_home_")
        self._orig = {
            key: os.environ.get(key)
            for key in ("HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME")
        }
        os.environ["HOME"] = self.home
        os.environ["XDG_CACHE_HOME"] = os.path.join(self.home, ".cache")
        os.environ["XDG_STATE_HOME"] = os.path.join(self.home, ".local", "state")
        self.calls = 0

    def tearDown(self):
        for key, orig in self._orig.items():
            if orig is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = orig
        shutil.rmtree(self.home, ignore_errors=True)

    def _fetch(self, value):
        def fetch():
            self.calls += 1
            return value

        return fetch

    def _set_marker(self, name):
        markers = os.path.join(os.environ["XDG_STATE_HOME"], "mackup", "markers")
        os.makedirs(markers, exist_ok=True)
        open(os.path.join(markers, name), "a").close()

    def test_newer_version_produces_a_line(self):
        line = update.check("2.1.0", fetch=self._fetch("2.2.0"), now=1000.0)
        assert line is not None
        assert "2.1.0" in line
        assert "2.2.0" in line

    def test_same_version_says_nothing(self):
        assert update.check("2.1.0", fetch=self._fetch("2.1.0"), now=1000.0) is None

    def test_older_remote_version_says_nothing(self):
        assert update.check("2.1.0", fetch=self._fetch("2.0.0"), now=1000.0) is None

    def test_pre_release_is_ignored(self):
        assert update.check("2.1.0", fetch=self._fetch("2.2.0rc1"), now=1000.0) is None

    def test_failed_fetch_says_nothing(self):
        assert update.check("2.1.0", fetch=self._fetch(None), now=1000.0) is None

    def test_raising_fetch_says_nothing(self):
        def boom():
            raise OSError("network down")

        assert update.check("2.1.0", fetch=boom, now=1000.0) is None

    def test_fresh_cache_suppresses_the_fetch(self):
        update.write_cache("2.2.0", 1000.0)
        line = update.check("2.1.0", fetch=self._fetch("9.9.9"), now=1000.0)
        assert self.calls == 0
        assert line is not None
        assert "2.2.0" in line

    def test_expired_cache_triggers_a_fetch_and_is_rewritten(self):
        update.write_cache("2.1.5", 1000.0)
        later = 1000.0 + update.CACHE_TTL_SECONDS + 1
        line = update.check("2.1.0", fetch=self._fetch("2.3.0"), now=later)
        assert self.calls == 1
        assert line is not None
        assert "2.3.0" in line
        assert update.read_cache(later) == "2.3.0"

    def test_marker_suppresses_everything(self):
        self._set_marker("no-update-check")
        update.write_cache("2.2.0", 1000.0)
        assert update.check("2.1.0", fetch=self._fetch("2.2.0"), now=1000.0) is None
        assert self.calls == 0

    def test_line_carries_the_upgrade_command(self):
        line = update.check("2.1.0", fetch=self._fetch("2.2.0"), now=1000.0)
        assert "Upgrade:" in line
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_update.py::TestCheck -v`
Expected: FAIL with `AttributeError: module 'mackup_ng.update' has no attribute 'check'`

- [ ] **Step 3: Add the fetch and the orchestration**

Extend the imports at the top of `src/mackup_ng/update.py`:

```python
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request
from typing import TYPE_CHECKING

from . import hooks, utils

if TYPE_CHECKING:
    from collections.abc import Callable

PYPI_URL: str = "https://pypi.org/pypi/mackup-ng/json"
NO_UPDATE_CHECK_MARKER: str = "no-update-check"
```

Append to the module:

```python
def fetch_latest(timeout: float = 2.0) -> str | None:
    """Ask PyPI for the latest published version, or None if we cannot.

    The only network call in mackup. Every failure mode — offline, timeout,
    an error status, malformed JSON, a missing field — collapses to None.
    """
    try:
        with urllib.request.urlopen(PYPI_URL, timeout=timeout) as response:
            payload = json.load(response)
        return str(payload["info"]["version"])
    except (OSError, ValueError, TypeError, KeyError):
        return None


def check(
    current: str,
    *,
    fetch: Callable[[], str | None] | None = None,
    now: float | None = None,
    verbose: bool = False,
) -> str | None:
    """Return the line to print about a newer release, or None.

    Consults the ``no-update-check`` marker first, then the cache, and only
    then the network. Never raises: a check that cannot answer says nothing.
    """
    if hooks.has_marker(NO_UPDATE_CHECK_MARKER):
        return None

    moment = time.time() if now is None else now
    latest = read_cache(moment)
    if latest is None:
        fetcher = fetch if fetch is not None else fetch_latest
        try:
            latest = fetcher()
        except Exception:  # noqa: BLE001 - a broken fetch must not break sync
            latest = None
        if latest is None:
            if verbose:
                print(utils.colorize_message("Update check skipped: PyPI unreachable"))
            return None
        write_cache(latest, moment)

    if not is_newer(latest, current):
        return None

    command = upgrade_command(sys.argv[0] if sys.argv else "")
    line = f"mackup-ng {current} -> {latest} available."
    if command is None:
        return line
    return f"{line} Upgrade: {command}"
```

Note on the `noqa`: the blanket `except` is deliberate and scoped to one call
— an injected or future fetch must never turn a sync into a traceback. The
narrow tuple stays on `fetch_latest` itself.

- [ ] **Step 4: Add the marker definition**

Create `src/mackup_ng/markers/no-update-check.toml`, matching the shape of the
existing definitions (`no-dconf.toml` is `[marker]` with `name` and `order`;
the shipped orders run 10, 20, 30, 40, 50):

```toml
[marker]
name = "opt-out: do NOT check PyPI for a newer mackup-ng on sync"
order = 60
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_update.py -v`
Expected: PASS (27 tests)

Then confirm the marker is visible:

Run: `uv run mackup-ng markers`
Expected: a `no-update-check` row appears among the opt-outs.

- [ ] **Step 6: Run the full suite, lint, and commit**

```bash
uv run pytest -q
uv run ruff check src tests
uv run mypy src
git checkout -- uv.lock 2>/dev/null || true
git add src/mackup_ng/update.py src/mackup_ng/markers/no-update-check.toml tests/test_update.py
git commit -m "feat: fetch the latest version from PyPI behind a cache and a marker"
```

---

### Task 3: Wire it into `sync` and document it

**Files:**
- Modify: `src/mackup_ng/main.py` (the end of the `sync` branch, after the dconf restore), `README.md`, `AGENTS.md`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `update.check(current, *, fetch=None, now=None, verbose=False) -> str | None` (Task 2); `constants.VERSION` (already imported in `main.py` as `from .constants import VERSION`).
- Produces: no new API — behavior and output only.

- [ ] **Step 1: Write the failing tests**

Add to the `TestCLI` class in `tests/test_cli.py`:

```python
def test_sync_reports_a_newer_release(self):
    buffer = io.StringIO()
    with (
        patch("sys.stdout", buffer),
        patch(
            "mackup_ng.update.fetch_latest",
            return_value="99.0.0",
        ),
        patch("sys.argv", ["mackup", "sync"]),
    ):
        main()
    assert "99.0.0 available" in buffer.getvalue()


def test_sync_says_nothing_when_up_to_date(self):
    buffer = io.StringIO()
    with (
        patch("sys.stdout", buffer),
        patch(
            "mackup_ng.update.fetch_latest",
            return_value="0.0.1",
        ),
        patch("sys.argv", ["mackup", "sync"]),
    ):
        main()
    assert "available" not in buffer.getvalue()


def test_dry_run_neither_fetches_nor_writes_the_cache(self):
    from mackup_ng import update

    calls = []

    def fetch(timeout=2.0):
        calls.append(timeout)
        return "99.0.0"

    with (
        patch("mackup_ng.update.fetch_latest", fetch),
        patch(
            "sys.argv",
            ["mackup", "-n", "sync"],
        ),
    ):
        main()
    assert calls == []
    assert not os.path.exists(update.cache_path())
```

These tests need `XDG_CACHE_HOME` inside the test's temp `HOME`; add it to
`TestCLI.setUp` next to the existing `XDG_CONFIG_HOME` line, and restore it in
`tearDown` the same way the other variables are restored:

```python
        os.environ["XDG_CACHE_HOME"] = os.path.join(self.test_home, ".cache")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k "newer_release or up_to_date or dry_run_neither" -v`
Expected: FAIL — nothing prints the notice, because `main.py` does not call the check yet.

- [ ] **Step 3: Call the check at the end of `sync`**

In `src/mackup_ng/main.py`, add `update` to the package import line:

```python
from . import blocks, dconf, hooks, mapping, update, utils
```

At the very end of the `sync` branch — after the dconf restore block that
begins `if role == "restore" and dconf_enabled:` — add:

```python
        # Last thing in a real run: one line if a newer release is out. A dry
        # run stays side-effect free, so it neither fetches nor writes a cache.
        if not dry_run:
            notice = update.check(VERSION, verbose=verbose)
            if notice:
                print(utils.colorize_message(notice))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -v`
Expected: PASS

- [ ] **Step 5: Document it in `README.md`**

Add a short section where the other sync-time behaviors are described, in the
README's voice:

```markdown
### Update check

At the end of `mackup sync`, mackup-ng checks whether a newer release is on
PyPI and prints one line if there is:

```text
mackup-ng 2.1.0 -> 2.2.0 available. Upgrade: uv tool upgrade mackup-ng
```

The upgrade command is guessed from where the running executable lives (snap,
uv tool, pipx, or plain pip). The result is cached under
`$XDG_CACHE_HOME/mackup/update-check.json` for a day, so at most one request a
day leaves the machine, and a dry run (`-n`) never checks at all. Any failure —
offline, timeout, a bad answer — is silent.

The request goes to `https://pypi.org/pypi/mackup-ng/json`. It carries nothing
but the package name, though like any request it discloses the machine's IP
address and the fact that mackup-ng is in use. To switch it off for good on a
machine:

```bash
mackup-ng mark no-update-check
```
```

- [ ] **Step 6: Document it in `AGENTS.md`**

Add the same facts in AGENTS.md's terser, code-oriented voice, near the other
sync-time behaviors:

```markdown
## Update check

`mackup sync` ends with `update.check(VERSION)` (`src/mackup_ng/update.py`),
which prints one line when PyPI has a newer release. The network lives in
`fetch_latest` alone; the result is cached in
`$XDG_CACHE_HOME/mackup/update-check.json` for 24 hours. Pre-releases are
ignored (`parse_version` accepts only dot-separated integers). Every failure is
silent, a dry run skips the check entirely, and the `no-update-check` marker
disables it — no fetch, no cache read, no output.
```

- [ ] **Step 7: Run the whole gate and commit**

```bash
uv run pytest -q
uv run rumdl check README.md AGENTS.md
make check
git checkout -- uv.lock 2>/dev/null || true
git add src/mackup_ng/main.py tests/test_cli.py README.md AGENTS.md
git commit -m "feat: report a newer release at the end of sync"
```

Note: `make check`'s `rumdl check .` step may report "No markdown files found
to check." in this environment — a pre-existing glob quirk, not a failure. The
explicit file list above is the real docs check.

---

## Self-Review Notes

Spec coverage check:

- Version parsing that rejects pre-releases, comparison, the install-method guess, the cache and its TTL → Task 1.
- The single network call, the marker opt-out, the cache-then-fetch orchestration, the message text → Task 2.
- Placement at the end of `sync`, the dry-run skip, and the documentation including the privacy note → Task 3.
