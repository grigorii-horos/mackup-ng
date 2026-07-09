# AGENTS.md

## Purpose

This fork of `mackup` adds path templating in application config definitions
(`src/mackup_ng/applications/*.cfg`). When editing or adding app configs, use the
fork features below to avoid duplicated platform-specific entries.

## Fork Path Templating (cfg files)

Path templating is supported in `[configuration_files]` entries.

### Built-in variables

Use these Mackup-specific variables (not OS env vars):

- `@CONFIG@` -> `.config` (Linux) / `Library/Application Support` (macOS) / `AppData/Roaming` (Windows)
- `@DATA@` -> `.local/share` (Linux) / `Library/Application Support` (macOS) / `AppData/Local` (Windows)
- `@STATE@` -> `.local/state` (Linux) / `Library/Application Support` (macOS) / `AppData/Local` (Windows)
- `@CACHE@` -> `.cache` (Linux) / `Library/Caches` (macOS) / `AppData/Local` (Windows)

### Platform selector

Use `[...]` to choose a path fragment by platform:

- Syntax: `[linux:...,mac:...,windows:...,fallback]`
- The last unkeyed item is the fallback.
- The fallback is also the canonical backup path used in the Mackup storage.

Selector semantics in this fork:

- local path = platform-specific value (or fallback if no matching key)
- backup path = fallback (or selected path if no fallback is provided)
- built-in variables in backup paths are resolved to Linux canonical values
  (`@CONFIG@ -> .config`, `@DATA@ -> .local/share`, etc.)

Examples:

- `@CONFIG@/[mac:Blender,blender]`
- `@CONFIG@/[mac:Sublime Text 3,sublime-text-3]/Packages/User`
- `[mac:@CONFIG@/MyApp/config.json,linux:@CONFIG@/myapp/config.json,@CONFIG@/shared/myapp-config.json]`

### Brace expansion

Use `{...}` to define multiple entries in one line:

- `@CONFIG@/Code/User/{snippets,keybindings.json,settings.json}`

Brace groups are expanded recursively (cartesian product when multiple groups
are present).

### Explicit local → backup mapping (`[mapped_files]`)

`[configuration_files]` keeps local path == storage layout. To decouple them,
use a `[mapped_files]` section with `LOCAL = BACKUP` pairs:

```ini
[mapped_files]
.config/app/grisa.profile/user.js = .config/app/profile/user.js
```

- LHS is the local file; RHS is where it lives in the backup folder.
- `=` is the ONLY delimiter — spaces, dashes, colons, even `->` are safe inside
  paths (read raw, not via ConfigParser; split on the first `=`).
- Both sides still honor selectors, built-in vars and braces (brace groups are
  zipped pairwise, so `.local/{a,b} = .backup/{a,b}` maps a→a, b→b).
- Lets machine-specific local paths (e.g. a per-machine Firefox profile dir)
  share one canonical backup path. Parsed in
  `appsdb._read_mapped_entries_from_section` + `_pair_to_exprs`.

## Processing Order

Paths are resolved in this order:

1. Platform selector `[...]`
   Produces local path and canonical backup path
2. Built-in variables (`@CONFIG@`, `@DATA@`, `@STATE@`, `@CACHE@`)
   Local path uses current OS mapping; backup path uses Linux canonical mapping
3. Brace expansion `{...}`

## Config Style (This Fork)

- Prefer `[configuration_files]` only.
- `xdg_configuration_files` is unsupported in this fork. Use `@CONFIG@/...` in `[configuration_files]`.
- Prefer a single templated line over duplicated macOS/Linux entries when
  semantics are the same.
- If macOS path naming differs, prefer a selector inside `@CONFIG@`, e.g.
  `@CONFIG@/[mac:Foo App,foo-app]`.
- Keep `Library/Preferences/...` entries as-is unless there is a clear
  cross-platform equivalent.

## Safety Notes

- Paths that start with selectors (e.g. `[mac:...,fallback]/...`) are supported
  by this fork, but they rely on fork-specific parsing logic in
  `src/mackup_ng/appsdb.py`.
- Upstream Mackup may not support these templates.

## Machine-local hooks, markers and config sets (`~/.mackup/`)

This fork turns `~/.mackup/` into the machine-local home for more than custom
app configs. Layout:

```
~/.mackup/
├── applications/   custom app *.cfg files (was ~/.mackup/*.cfg — now nested)
├── backup.d/       executables run BEFORE `mackup sync`
├── sets.d/         declarative config sets (TOML) applied natively after sync
├── markers/        machine-local condition flags (must NOT be synced)
├── state/          hook scratch space
└── dconf-backup/   dconf dumps (*.dconf)
```

Code: `src/mackup_ng/{hooks,sets,dconf}.py`. `constants.py` holds the dir names
(`CUSTOM_APPS_DIR = .mackup/applications`, `HOOKS_*_DIRNAME`, `MARKERS_DIRNAME`,
`SETS_DIRNAME`, `STATE_DIRNAME`, `DCONF_DIRNAME`).

### `mackup sync` phases

1. `hooks.run_hooks("backup")` — backup.d executables (if any).
2. dconf dump (backup-role machine).
3. file sync (mackup engine).
4. dconf load (restore-role machines).
5. `sets.apply_dir(~/.mackup/sets.d)` — declarative sets (was restore.d hooks).

Backup.d hooks + `[[run]]` scripts receive a `MACKUP_*` environment contract
(`hooks.hook_env`): `MACKUP_PHASE`, `MACKUP_ROLE` (backup if the `backup` marker
exists, else restore), `MACKUP_OS`, `MACKUP_ARCH`, `MACKUP_HAS_GUI`,
`MACKUP_CONFIG_DIR` (=`~/.mackup`), `MACKUP_MARKERS_DIR`, `MACKUP_STATE_DIR`,
`MACKUP_DCONF_BACKUP_DIR`.

### Markers CLI

- `mackup mark <name>`   — set a marker (known: backup, low-resource, no-linger,
  no-apikey, no-dconf).
- `mackup unmark <name>` — remove a marker.
- `mackup markers`       — list known + active markers.

Names are validated (`A-Z a-z 0-9 . _ -`, no `.`/`..`). Registry:
`hooks.KNOWN_MARKERS` / `KNOWN_ORDER`.

### Config sets (`sets.d/`, `sets.py`)

Declarative TOML sets, applied **natively** by `sets.apply_dir` during sync (and
via `mackup apply-sets`). No external engine. Each set is gated by
`require_marker` / `skip_if_marker` / `require_os` and may carry:

- `files` + `files_mode`, `restart_service`, `source_env`
- `restart_service` — user service bracketed around the write: **systemctl
  --user** on linux, **brew services** on macOS (stop before / start after,
  only if active). `before` / `after` — arbitrary shell run before / after the
  write (custom stop/start escape hatch; `after` is guaranteed).
- `[[systemd_dropin]]` — write a user systemd drop-in (linux-only by default;
  narrow with per-block `require_os`).
- `[[mutate_xml]]` — XML edits (`select`, `set_attr`, `set_child`,
  `create_missing`, `create_parents`). Cross-platform (works on macOS too).
- `[[run]]` — inline shell (`script`, `shell`, `require_command`) for imperative
  bits that can't be declarative (e.g. `10-enable-linger.toml` runs
  `loginctl enable-linger`). Runs with the `MACKUP_*` env; must be idempotent.
  `require_command` skips the run unless the given binary/binaries are on PATH.

Files are applied **sorted by name** — use numeric prefixes (`10-`, `20-`, ...).
XML/drop-in writes are idempotent (no write / no service restart when already
identical); `[[run]]` scripts always execute. When XML changed but no service
manager is available (e.g. no systemctl/brew), a warning tells the user to
restart the service manually. (The former external `~/.bin/.sync-sets` script is
superseded by this module.)

### dconf (`dconf.py`)

Native dconf backup/restore (Linux/GNOME). Tracked paths are stored as
`~/.mackup/dconf-backup/*.dconf` dumps (file name `org.gnome.terminal.dconf`
<-> path `/org/gnome/terminal/`). On `mackup sync`: the backup-role machine
dumps each path before the file sync; restore-role machines load them after.
Gated off by the `no-dconf` marker. Register a path with
`mackup dconf-add /org/gnome/terminal/`.
