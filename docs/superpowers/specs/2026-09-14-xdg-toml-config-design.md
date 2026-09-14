# XDG layout + TOML configuration

Date: 2026-09-14
Status: approved for planning

## Goal

Move every mackup-ng file out of `~/.mackup` and `~/.mackup.cfg` into the XDG
base directories, and replace the INI config file with TOML.

Two motivations:

1. **Format.** Application profiles, ignore definitions and marker definitions
   are already TOML parsed with `tomllib`. The main config file is the last
   INI holdout, and `configparser` exists in the codebase only to serve it.
2. **Location.** `appsdb.py` and `ignore.py` already search an XDG directory
   *in addition to* `~/.mackup`, and marker state already lives only in
   `$XDG_STATE_HOME`. The dual lookup is transitional scaffolding. Committing
   to XDG removes it; committing to `~/.mackup` would mean deleting the newer
   half instead.

## Non-goals

- No backwards compatibility. The old INI parser is deleted, not deprecated.
  There is no transitional release that reads both locations: the only machine
  using this fork is the author's.
- No change to how application profiles, blocks, conditions or the sync
  algorithm work. Only *where* files are found and *how* the main config is
  parsed.
- No change to `$XDG_STATE_HOME/mackup/markers/` — marker state is already in
  the right place.

## Target layout

```
$XDG_CONFIG_HOME/mackup/          (~/.config/mackup)        synced
    config.toml                   main config
    applications/*.toml           application profiles
    ignores/*.toml                ignore definitions
    markers/*.toml                marker DEFINITIONS

$XDG_DATA_HOME/mackup/            (~/.local/share/mackup)   synced
    dconf-backup/*.dconf          dconf dumps

$XDG_STATE_HOME/mackup/           (~/.local/state/mackup)   NOT synced
    markers/                      marker state flags (machine-local)
    sync-log.json                 per-machine record of the last sync
```

`~/.mackup` and `~/.mackup.cfg` cease to exist and are never read.

The split follows the XDG base directory spec literally: configuration the
user edits goes to `XDG_CONFIG_HOME`; dconf dumps are generated content, so
they are data; marker flags describe *this machine* and are state.

## Config file format

`~/.config/mackup/config.toml`:

```toml
[storage]
engine = "file_system"      # dropbox | google_drive | icloud | file_system
path = "Sync/Configs"       # required for file_system, ignored otherwise
directory = "Mackup"        # optional, defaults to "Mackup"

[applications]
ignore = ["ssh", "bash", "git"]
sync = []                   # empty = sync every supported application
```

Mapping from the old INI format:

| INI | TOML |
| --- | --- |
| `[storage] engine = dropbox` | `[storage] engine = "dropbox"` |
| `[storage] path = Sync/Configs` | `[storage] path = "Sync/Configs"` |
| `[storage] directory = Mackup` | `[storage] directory = "Mackup"` |
| `[applications_to_ignore]` + bare keys | `[applications] ignore = [...]` |
| `[applications_to_sync]` + bare keys | `[applications] sync = [...]` |
| `[colors]` | dropped — never read by any code |

The INI sections used bare option names as a set; TOML expresses that as an
array of strings. The public `Config` API is unchanged: `engine`, `path`,
`directory`, `fullpath`, `apps_to_ignore`, `apps_to_sync` keep their current
names, types and semantics, so no consumer of `Config` is touched.

An absent config file is not an error. As today, it yields defaults
(`engine = "dropbox"`, `directory = "Mackup"`, empty ignore/sync sets).

## New module: `dirs.py`

The "read `XDG_*_HOME`, else fall back" logic currently exists in three places
with two different, non-equivalent spellings:

- `appsdb.py:564` — `os.environ.get("XDG_CONFIG_HOME", os.path.join(home, ".config"))`
- `ignore.py:47` — `os.environ.get("XDG_CONFIG_HOME") or os.path.join(home, ".config")`
- `hooks.py:72` — `os.environ.get("XDG_STATE_HOME", os.path.join(home, ".local", "state"))`

With `XDG_CONFIG_HOME=""` the first builds a path from the filesystem root
while the second falls back to `~/.config`. A single implementation removes
the divergence.

`src/mackup_ng/dirs.py` becomes the only place that resolves a base directory:

```python
def config_dir() -> str          # $XDG_CONFIG_HOME/mackup
def data_dir() -> str            # $XDG_DATA_HOME/mackup
def state_dir() -> str           # $XDG_STATE_HOME/mackup

def config_file() -> str         # config_dir()/config.toml
def custom_apps_dir() -> str     # config_dir()/applications
def custom_ignores_dir() -> str  # config_dir()/ignores
def custom_markers_dir() -> str  # config_dir()/markers
def markers_state_dir() -> str   # state_dir()/markers
def dconf_backup_dir() -> str    # data_dir()/dconf-backup
```

Base resolution rule, applied identically to all three variables: use the
environment value only when it is non-empty **and** absolute; otherwise use
the default (`~/.config`, `~/.local/share`, `~/.local/state`). Rejecting
relative values is what the XDG spec requires and is not currently done
anywhere.

`constants.py` keeps the bare directory names (`applications`, `ignores`,
`markers`, `dconf-backup`, `config.toml`) and stops joining them onto `$HOME`.
These constants are deleted: `MACKUP_HOME_DIR`, `CUSTOM_APPS_DIR`,
`CUSTOM_APPS_DIR_XDG`, `CUSTOM_IGNORES_DIR`, `IGNORES_DIR_XDG`,
`CUSTOM_MARKERS_DIR`, `MARKERS_STATE_XDG`, `LEGACY_MARKERS_STATE_DIR`.

## Module changes

### `config.py`

- `configparser` import and `_setup_parser` are removed. `_load(path) -> dict`
  reads the file with `tomllib`; a missing file yields `{}`.
- `_parse_engine`, `_parse_path`, `_parse_directory`, `_parse_apps_to_ignore`
  and `_parse_apps_to_sync` read nested dicts instead of `parser.get` /
  `parser.options`.
- `_best_config_path` collapses to: `--config-file` when given, else
  `dirs.config_file()`. The `$MACKUP_CONFIG` variable and the
  `$XDG_CONFIG_HOME/mackup/mackup.cfg` lookup are removed.
- The existing `--config-file` guards are kept: the file must exist, and it
  must live under `$HOME`.
- `_warn_on_old_config` is replaced by `_reject_legacy_layout`, which errors
  when `~/.mackup.cfg` or `~/.mackup` exists, naming the new location and the
  format change. The old `[Allowed Applications]` / `[Ignored Applications]`
  check goes away with it — those sections cannot occur in a TOML file.
- `_parse_directory`'s guard currently rejects two hard-coded custom-apps path
  strings. It is replaced by a containment check: resolve `fullpath` and
  reject it if it lies inside `config_dir()`, `data_dir()` or `state_dir()`.
  This covers more cases than the string comparison and does not need updating
  when a directory is renamed.

### `appsdb.py`

`get_config_files()` currently returns stock → XDG custom → `~/.mackup`
custom, with a three-way name-shadowing computation. It becomes stock →
`dirs.custom_apps_dir()`. Custom files still shadow a stock file of the same
name entirely, and later files still win on duplicate destinations.

### `ignore.py`

`_custom_ignores_dirs()` returns a two-element list today; it becomes a single
path from `dirs.custom_ignores_dir()`, and callers lose their loop.

### `hooks.py`

- `mackup_home()` is deleted.
- `custom_markers_dir()` and `markers_dir()` delegate to `dirs.py`.
- `_migrate_legacy_state()` and its call site are deleted — there is no legacy
  directory left to migrate from. The one-time migration of the author's own
  flag files happens in the migration runbook instead.
- The module docstring's layout diagram is rewritten for the new three-root
  layout.

### `dconf.py`

`dconf_backup_dir()` delegates to `dirs.dconf_backup_dir()`.

## Self-sync profile

`src/mackup_ng/applications/mackup.toml` (and the sample copy under `doc/`):

```toml
name = "Mackup"
files = [
    ".config/mackup",
    ".local/share/mackup",
]
```

This syncs the config, application profiles, ignore definitions, marker
definitions and dconf dumps.

`.local/state/mackup` is deliberately absent. Marker flags mark *this* machine
— syncing them would carry the `backup` role to another machine and invert the
direction of the next sync.

> **Correction (final review, 2026-09-14):** the paragraph below originally
> claimed mackup syncs by moving a path into storage and leaving a symlink
> behind. That is false for this codebase and was never true of it — see the
> Risks section for the full correction. Kept, struck through, so the
> reasoning that motivated `config.py`'s loud legacy-layout error is visible
> rather than silently rewritten.

~~Known constraint, unchanged from the current behaviour: mackup syncs by
moving a path into the storage folder and leaving a symlink behind, so
`~/.config/mackup` becomes a symlink into `~/Sync/Configs/Mackup`, and mackup
reads its own config through it. If that symlink breaks, no config is found
and mackup silently falls back to the `dropbox` engine.~~ In fact
`Application.sync_members_directory()` (`src/mackup_ng/application.py`)
performs a union directory merge — newest entry wins, copied file by file —
between every member (`~/.config/mackup` and the copy under the storage
folder). Both stay real directories after a sync; neither is a symlink.
There is no broken-symlink failure mode: at worst, a member missing or
unreadable at sync time simply does not receive that sync's changes. This is
a pre-existing property of syncing `~/.mackup` and is not made worse here,
but it is the reason the legacy-layout check in `config.py` errors loudly
rather than warning.

## Hook environment contract

| Variable | Before | After |
| --- | --- | --- |
| `MACKUP_CONFIG_DIR` | `~/.mackup` | `~/.config/mackup` |
| `MACKUP_MARKERS_DIR` | `~/.local/state/mackup/markers` | unchanged |
| `MACKUP_DCONF_BACKUP_DIR` | `~/.mackup/dconf-backup` | `~/.local/share/mackup/dconf-backup` |
| `MACKUP_DATA_DIR` | — | `~/.local/share/mackup` (new) |
| `MACKUP_STATE_DIR` | — | `~/.local/state/mackup` (new) |
| `MACKUP_PHASE`, `MACKUP_ROLE`, `MACKUP_OS`, `MACKUP_ARCH`, `MACKUP_HAS_GUI`, `MACKUP_BACKUP_DIR` | | unchanged |

Verified before writing this spec: the author's own `sets.d/*.toml`,
`applications/*.toml` and `markers/*.toml` reference only `MACKUP_OS`,
`MACKUP_ARCH` and `MACKUP_XDG_CONFIG`, none of which change.

## Error handling

| Situation | Behaviour |
| --- | --- |
| No config file | Defaults; not an error (unchanged) |
| `~/.mackup.cfg` or `~/.mackup` present | `error()` naming the new path and format |
| Malformed TOML | `error()` with the file path and `str(exc)` from `tomllib.TOMLDecodeError` |
| `applications.ignore` / `.sync` not a list of strings | `ConfigError` naming the key and the type found |
| `storage.engine` unknown | `ConfigError` (unchanged) |
| `file_system` engine without `path` | `ConfigError` (unchanged) |
| Unknown table or key | Warning, not an error, naming the key |

The unknown-key warning is new. `[colors]` sat in the author's config being
silently ignored for as long as it existed; a typo such as `applications.ignor`
would fail the same silent way.

`utils` has no warning helper — only `error()`. Warnings follow the
established pattern, `print(utils.colorize_message("Warning: ..."))`, matching
the unrecognized-key warning in `appsdb.py:436`. The TOML parse message
follows `blocks.py:390`, which already formats `TOMLDecodeError` as
`f"cannot parse {path}: {exc}"`; `TOMLDecodeError.lineno` is not usable
because it only exists from Python 3.14 and the project targets 3.12+.

## Migration runbook

One-time, performed by hand after implementation; not code that ships.

1. `~/.mackup/applications/` → `~/.config/mackup/applications/` (18 files)
2. `~/.mackup/ignores/` → `~/.config/mackup/ignores/` (if present)
3. `~/.mackup/markers/*.toml` → `~/.config/mackup/markers/` (`eink.toml`)
4. `~/.mackup/markers/{backup,low-resource}` — extensionless state flags still
   sitting in the pre-XDG location — → `~/.local/state/mackup/markers/`
5. `~/.mackup/dconf-backup/` → `~/.local/share/mackup/dconf-backup/` (36K)
6. `~/.mackup.cfg` → `~/.config/mackup/config.toml`, converted per the mapping
   table above, dropping `[colors]`
7. `~/.mackup/{CLAUDE.md,GEMINI.md,patch.py,.codex/}` → `~/.config/mackup/` —
   not mackup-ng files, but they live in that directory; the path reference in
   the first line of `CLAUDE.md` is updated
8. Deleted, not moved: `~/.mackup/sets.d/` (7 files — dead since
   `sets.apply_dir` was removed in the unified-config-blocks change),
   `~/.mackup/backup.d/` and `~/.mackup/state/` (both empty)
9. Remove the stale `.mackup` and `.mackup.cfg` copies from
   `~/Sync/Configs/Mackup/`, then re-run sync so the new paths are adopted
10. Verify with `mackup list` and `mackup info` before removing `~/.mackup`

## Tests

Rewritten:

- 13 fixtures `tests/fixtures/*.cfg` → `.toml`, plus the inline config writes
  in `test_ignore_sync.py`, `test_appsdb_xdg.py`, `test_markers.py`,
  `test_info.py`, `test_rm_destinations.py`, `test_config_conditions.py`,
  `test_sync_groups.py`, `test_config_file_option.py`, `test_cli.py`,
  `test_config.py`.
- `test_appsdb_xdg.py` asserts the legacy-over-XDG precedence that no longer
  exists; it becomes a single-custom-directory test.

Deleted: `test_config_envvar`, `test_config_xdg` (both test removed lookups),
and the marker legacy-state-migration tests.

Added:

- `~/.mackup.cfg` present → error mentioning `config.toml`
- `~/.mackup` present → same
- malformed TOML → error naming the file, no traceback
- `applications.ignore = "ssh"` (string, not list) → `ConfigError`
- unknown table → warning, config still parses
- `XDG_CONFIG_HOME=""` and `XDG_CONFIG_HOME="relative/path"` both fall back to
  `~/.config` — covering the divergence `dirs.py` fixes
- `storage.directory` resolving inside `config_dir()`/`data_dir()`/`state_dir()`
  → `ConfigError`

## Documentation

- `doc/README.md` — config location and format sections
- `doc/.mackup.cfg` → `doc/config.toml`, rewritten as TOML
- `doc/.mackup/` → `doc/config/` (sample profiles)
- `doc/configuration_merge_guide.md` — `.mackup.cfg` references
- `doc/ARCHITECTURE.md` — layout
- `README.md` — layout and the env-contract list
- Docstrings in `main.py` (usage text) and `hooks.py` (layout diagram)

## Risks

- ~~**Broken self-sync symlink yields silent dropbox fallback.** Pre-existing;
  mitigated only by the loud legacy-layout error. Accepted.~~
  **Correction (final review, 2026-09-14):** this risk does not exist. It
  assumed mackup syncs `~/.config/mackup` by replacing it with a symlink into
  storage, so a broken link would make `Config()` find nothing and silently
  default to the `dropbox` engine. `src/mackup_ng/application.py` (around
  `sync_members_directory`, lines 210-290) instead performs a union directory
  merge with newest-entry-wins copies — `grep -rn symlink src/` returns only
  two unrelated diagnostic strings, in `utils.py` and `info.py`. After a
  sync, `~/.config/mackup` remains a real directory and the storage folder
  holds a real copy beside it; there is no single link whose breakage could
  cause a silent fallback. The loud legacy-layout error in `config.py`
  remains correct — it protects against the legacy `~/.mackup`/`~/.mackup.cfg`
  layout being mistaken for "nothing configured", not against a broken
  symlink.
- **Wide blast radius in tests.** Ten test modules write config inline. The
  risk is a mechanical translation error rather than a design flaw; the test
  suite itself is the check.
- **Migration is manual and touches live synced data.** Step 9 removes files
  from the sync storage.
  **Correction (final review, 2026-09-14):** the runbook cannot verify with
  `mackup list` / `mackup info` *before* `~/.mackup` is deleted, as this bullet
  originally claimed — `Config._reject_legacy_layout` tests existence, not
  emptiness, so every command aborts loudly while `~/.mackup` still exists,
  regardless of what has already been moved out of it. Verification now runs
  *after* the legacy paths are removed (runbook step 8, formerly step 7); the
  safety copy taken in step 1, plus moving every file mackup cares about out
  of `~/.mackup` before it is removed (steps 3-6), is what makes deleting it
  first safe. The point of no return is step 7 (`rm -rf ~/.mackup
  ~/.mackup.cfg`, formerly step 8), not the verification step.
