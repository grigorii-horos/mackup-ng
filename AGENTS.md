# AGENTS.md

## Purpose

This fork of `mackup` adds path templating in application config definitions
(`src/mackup_ng/applications/*.toml`). App definitions are **flat TOML** (not the
upstream INI `.cfg`, and no `[application]` wrapper): top-level `name` and a
`files` array, plus an optional `[mapped_files]` table and action
blocks (see "Action blocks"):

```toml
name = "Code"
files = [
    "${MACKUP_XDG_CONFIG}/Code/User/{snippets,keybindings.json,settings.json}",
    "[mac:Library/Application Support/Code,~/.config/Code]/User/tasks.json",
]

[mapped_files]
".config/app/grisa.profile/user.js" = ".config/app/profile/user.js"
```

A legacy `[application]` table (with `name`/`files`/`source_env`
inside) is still accepted as a fallback, but new configs use the flat form.

When editing or adding app configs, use the fork features below to avoid
duplicated platform-specific entries.

## Fork Path Templating (toml files)

Path templating is supported in `files` array entries (and both
sides of `[mapped_files]`). Selectors/braces/vars are just text inside the TOML
strings, so no escaping quirks apply.

### Built-in XDG variables (dual-path, `${MACKUP_XDG_*}`)

These are reserved Mackup-owned variables (NOT env vars) that mimic the XDG base
directories on **every** OS — even systems that don't set the `XDG_*` env vars.
The `MACKUP_` prefix keeps them from clashing with a real `${VAR}` env
reference; `XDG` marks the semantics. Each resolves to a platform-specific
**local** path and a canonical Linux **backup** path:

- `${MACKUP_XDG_CONFIG}` -> `.config` (Linux) / `Library/Application Support` (macOS) / `AppData/Roaming` (Windows)
- `${MACKUP_XDG_DATA}` -> `.local/share` (Linux) / `Library/Application Support` (macOS) / `AppData/Local` (Windows)
- `${MACKUP_XDG_STATE}` -> `.local/state` (Linux) / `Library/Application Support` (macOS) / `AppData/Local` (Windows)
- `${MACKUP_XDG_CACHE}` -> `.cache` (Linux) / `Library/Caches` (macOS) / `AppData/Local` (Windows)

### Environment variables (`${VAR}` + `source_env`)

Any `${VAR}` whose name is **not** a reserved `MACKUP_*` built-in is resolved as
a real environment variable, falling back to `KEY=VALUE` lines in the files
listed in the app's `source_env`. Unlike the built-ins, an env var expands to
the **same** value on both the local and backup side, so use it only for
relative, machine-specific fragments (a profile dir name, a hostname). An
unresolved var, or one that yields an absolute path, skips just that entry.

```toml
name = "Firefox"
source_env = ["~/.config/mackup-env"]   # KEY=VALUE lines; env takes priority
files = [
    "${MACKUP_XDG_CONFIG}/firefox/${FF_PROFILE}/prefs.js",
]
```

Resolved by `appsdb._expand_env_vars` / `_lookup_source_env`.

### Platform selector

Use `[...]` to choose a path fragment by platform:

- Syntax: `[linux:...,mac:...,windows:...,fallback]`
- The last unkeyed item is the fallback.
- The fallback is also the canonical backup path used in the Mackup storage.

Selector semantics in this fork:

- local path = platform-specific value (or fallback if no matching key)
- backup path = fallback (or selected path if no fallback is provided)
- built-in variables in backup paths are resolved to Linux canonical values
  (`${MACKUP_XDG_CONFIG} -> .config`, `${MACKUP_XDG_DATA} -> .local/share`, etc.)

Examples:

- `${MACKUP_XDG_CONFIG}/[mac:Blender,blender]`
- `${MACKUP_XDG_CONFIG}/[mac:Sublime Text 3,sublime-text-3]/Packages/User`
- `[mac:${MACKUP_XDG_CONFIG}/MyApp/config.json,linux:${MACKUP_XDG_CONFIG}/myapp/config.json,${MACKUP_XDG_CONFIG}/shared/myapp-config.json]`

### Brace expansion

Use `{...}` to define multiple entries in one line:

- `${MACKUP_XDG_CONFIG}/Code/User/{snippets,keybindings.json,settings.json}`

Brace groups are expanded recursively (cartesian product when multiple groups
are present).

### Explicit local → backup mapping (`[mapped_files]`)

`files` keeps local path == storage layout. To decouple them,
use a `[mapped_files]` table of `LOCAL = BACKUP` pairs (TOML quoted keys/values):

```toml
[mapped_files]
".config/app/grisa.profile/user.js" = ".config/app/profile/user.js"
```

- Key is the local file; value is where it lives in the backup folder.
- Paths are quoted TOML strings, so spaces, dashes, colons, even `->` are safe.
- Both sides still honor selectors, built-in vars and braces (brace groups are
  zipped pairwise, so `.local/{a,b} = .backup/{a,b}` maps a→a, b→b).
- Lets machine-specific local paths (e.g. a per-machine Firefox profile dir)
  share one canonical backup path. Parsed from the `[mapped_files]` table in
  `appsdb.__init__` + `_pair_to_exprs`.

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
  source is deleted only when no destination is left. Removing a path *inside*
  a managed directory removes it from every member of that directory's group
  (source and sibling destinations), otherwise the next merge would copy it
  back from a sibling.

## Processing Order

Paths are resolved in this order:

1. Platform selector `[...]`
   Produces local path and canonical backup path
2. Built-in variables (`${MACKUP_XDG_CONFIG}`, `${MACKUP_XDG_DATA}`, `${MACKUP_XDG_STATE}`, `${MACKUP_XDG_CACHE}`)
   Local path uses current OS mapping; backup path uses Linux canonical mapping
3. Environment variables (any other `${VAR}`, from env / `source_env`)
   Same value on both sides; unresolved or absolute → entry skipped
4. Brace expansion `{...}`

## Config Style (This Fork)

- Prefer `files` only.
- Upstream `xdg_configuration_files` is unsupported. Use `${MACKUP_XDG_CONFIG}/...` in `files`.
- Prefer a single templated line over duplicated macOS/Linux entries when
  semantics are the same.
- If macOS path naming differs, prefer a selector inside `${MACKUP_XDG_CONFIG}`, e.g.
  `${MACKUP_XDG_CONFIG}/[mac:Foo App,foo-app]`.
- Keep `Library/Preferences/...` entries as-is unless there is a clear
  cross-platform equivalent.

## Safety Notes

- Paths that start with selectors (e.g. `[mac:...,fallback]/...`) are supported
  by this fork, but they rely on fork-specific parsing logic in
  `src/mackup_ng/appsdb.py`.
- Upstream Mackup may not support these templates.

## Machine-local markers and action blocks (`~/.mackup/`)

This fork turns `~/.mackup/` into the machine-local home for more than custom
app configs. Layout:

```text
~/.mackup/
├── applications/   config *.toml files: sync lists AND action blocks
├── markers/        LOCAL marker definitions (<name>.toml — same format as apps)
└── dconf-backup/   dconf dumps (*.dconf)
```

There is no separate `sets/` or `backup.d/` directory: everything imperative is
an action block inside an `applications/*.toml` config (a file may be sync-only,
block-only, or both). A pre-sync executable is just a `[run]` block with
`phase = "pre"`. See "Action blocks" below.

Marker **state** (which markers are on) is machine-local runtime state and lives
in `$XDG_STATE_HOME/mackup/markers/` (default `~/.local/state/mackup/markers/`),
NOT under `~/.mackup/`. A pre-XDG `~/.mackup/markers/` state dir is migrated into
the XDG location automatically on first marker access (`_migrate_legacy_markers`).

Code: `src/mackup_ng/{appsdb,blocks,conditions,hooks,dconf}.py`. `constants.py`
holds the dir names (`CUSTOM_APPS_DIR = .mackup/applications`,
`CUSTOM_MARKERS_DIR = .mackup/markers`, `MARKERS_DEFS_DIRNAME` (package
built-ins), `MARKERS_STATE_XDG`, `LEGACY_MARKERS_STATE_DIR`, `DCONF_DIRNAME`).

### `mackup sync` phases

1. dconf dump (backup-role machine).
2. per config (sorted by id): `blocks.apply_blocks(phase="pre")` → file sync →
   `blocks.apply_blocks(phase="post")`.
3. dconf load (restore-role machines).

`mackup apply` runs every config's blocks (pre+post) without syncing files.

`[run]` blocks receive a `MACKUP_*` environment contract (`hooks.hook_env`):
`MACKUP_ROLE` (backup if the `backup` marker exists, else restore), `MACKUP_OS`,
`MACKUP_ARCH`, `MACKUP_HAS_GUI`, `MACKUP_CONFIG_DIR` (=`~/.mackup`),
`MACKUP_BACKUP_DIR` (=`Config.fullpath`, the storage folder Mackup syncs into —
resolves the configured engine/path, so configs never hard-code
`~/Sync/Configs/Mackup`), `MACKUP_MARKERS_DIR`, `MACKUP_DCONF_BACKUP_DIR`.

### Markers CLI

- `mackup mark <name>` — set a marker (write a flag into the XDG state dir).
- `mackup unmark <name>` — remove a marker.
- `mackup markers` — list known + active markers.

Names are validated (`A-Z a-z 0-9 . _ -`, no `.`/`..`). Any name can be set
(custom markers are allowed); the *known* set is just what has a definition.

**Definitions** are one `<name>.toml` per marker in the **same TOML format as app
definitions** (`tomllib`): a `[marker]` table with `name` (human label, mirrors
the app `name` key) and optional `order`. Discovered like app defs — built-ins
ship in the package (`src/mackup_ng/markers/*.toml`: backup, low-resource,
no-linger, no-apikey, no-dconf), local ones live in `~/.mackup/markers/*.toml`
and override a built-in of the same name. Loaded by `hooks.load_marker_defs()`;
`markers_report()` lists them sorted by `order` then name.

### Action blocks (`blocks.py`, `conditions.py`)

Action blocks live in `applications/*.toml` config files (parsed by `appsdb`,
executed by `blocks.apply_blocks`). A **block** = base keys (phase, conditions,
`restart_service`) + exactly **one action sub-table** whose name selects the
action — there is no `type` key. A config's blocks are: the top-level implicit
block (base keys + one action table at the top level — being top-level, they
must precede any `[mapped_files]`/`[[block]]` table, per TOML) then the
`[[block]]` array entries, in that order.

Full example — a hybrid config (syncs `.ssh`, then fixes its perms; plus a
marker-gated extra block):

```toml
name = "SSH"
files = [".ssh"]

# top-level block: chmod the just-synced .ssh (default phase = post)
[when]
os = ["linux", "macos"]
[chmod]
path = "~/.ssh"
recursive = true
dir_mode = "700"
file_mode = "600"

# a second block (array form): only with the `paranoid` marker
[[block]]
[block.when]
marker = ["paranoid"]
[block.run]
commands = ["ssh-add -l"]
```

Base scalars (on the block itself):

- `phase` — `pre` | `post` (default `post`): before / after this config's file
  sync.
- `restart_service` — user service bracketed around this block's action
  (**systemctl --user** on linux, **brew services** on macOS; only if active).

Conditions sub-table `[when]` (`conditions.block_passes`; any-of lists; short
keys — the section supplies the context). A block runs only if every condition
in its `[when]` passes (no file-level inheritance — the top level is itself a
block):

- `os`, `arch` — current OS / `platform.machine()` in list.
- `marker`, `not_marker` — every / none of the listed markers set.
- `command` — every listed binary on PATH.
- `gui` — `true` and `MACKUP_HAS_GUI`.
- `exists`, `not_exists` — every / none of the listed paths exist.
- `env` — list → every var set; table → every var equals its value.

Action sub-tables (exactly one per block; the key is the action):

- `[copy]` — `from`, `to` (honor `~`, `$VAR`/`${VAR}` from the `MACKUP_*` env, so
  `$MACKUP_BACKUP_DIR/...`, `~/.apps/$MACKUP_ARCH`). File source idempotent
  (unlinks dest first — never writes through a symlink); directory source merges
  (`cp -RpLf src/. dst`, follows symlinks) and is always applied.
- `[chmod]` — roots via `path`/`paths` (`~`/`$VAR`/globs). Octal (`"700"`) or
  symbolic-add (`"+x"`). Either `mode` (the root) **or** `recursive = true` with
  `dir_mode` / `file_mode` per directory / file. Idempotent; missing paths
  skipped.
- `[run]` — `script` (heredoc) or `commands` (list, stops at first failure);
  `shell`. Always executes when its block's conditions pass; must be idempotent.
- `[xml]` — `paths` + `paths_mode`, `select`, `set_attr`, `set_child`,
  `create_missing`, `create_parents`. Cross-platform. Idempotent (no write / no
  restart when already identical).
- `[systemd]` — `service`, `name`, `Environment` (+ `MemoryMax`/`CPUQuota`/
  `Nice`). Gate with `[when]` `os = ["linux"]`.

In the `[[block]]` array, sub-tables are written `[block.when]` /
`[block.<action>]`; at the top level they are just `[when]` / `[<action>]`.

Configs are processed **sorted by filename** — cross-cutting hooks use numeric
prefixes (`10-`, `40-`). `${VAR}` (non-`MACKUP_*`) in block values resolves from
the environment / a top-level `source_env` list. (The former `~/.bin/.sync-sets`
script and the separate `sets/` directory are superseded by this model.)

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

**Top-level keys must come before any table header.** TOML assigns a bare
`key = value` line to whichever table was most recently opened above it, not
to the document root. If `files = [...]` is written *after* the `[when]`
header, it parses as `when.files` instead of the config's own `files` — the
config then silently declares no synced files at all. `appsdb.py` warns about
this at load time: an unrecognized key inside `[when]` prints `Warning:
{app}: unrecognized [when] key(s): ...`, and a `[when]` that isn't a table at
all (e.g. `when = "android"`) prints `Warning: {app}: top-level [when] must
be a table, ignoring` and is treated as no conditions. Put every top-level
key (`name`, `files`, `configuration_files`, `mapped_files`, `source_env`)
above the `[when]` header, or in its own table before it.

### dconf (`dconf.py`)

Native dconf backup/restore (Linux/GNOME). Tracked paths are stored as
`~/.mackup/dconf-backup/*.dconf` dumps (file name `org.gnome.terminal.dconf`
<-> path `/org/gnome/terminal/`). On `mackup sync`: the backup-role machine
dumps each path before the file sync; restore-role machines load them after.
Gated off by the `no-dconf` marker. Register a path with
`mackup dconf-add /org/gnome/terminal/`.

## Update check

`mackup sync` ends with `update.check(VERSION)` (`src/mackup_ng/update.py`),
which prints one line when PyPI has a newer release. The network lives in
`fetch_latest` alone; the result is cached in
`$XDG_CACHE_HOME/mackup/update-check.json` for 24 hours. Pre-releases are
ignored (`parse_version` accepts only dot-separated integers). Every failure is
silent, a dry run skips the check entirely, and the `no-update-check` marker
disables it — no fetch, no cache read, no output.
