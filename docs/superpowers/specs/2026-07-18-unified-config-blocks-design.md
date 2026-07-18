# Unified config format: merge `applications` and `sets` into one file with blocks

**Date:** 2026-07-18
**Status:** approved design, pending implementation plan

## Problem

mackup-ng currently splits a "thing to manage" across two unrelated file types:

- **`applications/*.toml`** (package + `~/.mackup/applications/`) — declares
  *what to sync*: `[application]` with `name`, `configuration_files`, and an
  optional `[mapped_files]` table. Loaded by `appsdb.py`, synced bidirectionally
  by `application.py`.
- **`sets/*.toml`** (`~/.mackup/sets/`) — declares *post-sync actions*:
  `[[copy]]`, `[[chmod]]`, `[[run]]`, `[[mutate_xml]]`, `[[systemd_dropin]]`,
  gated by `require_marker` / `skip_if_marker` / `require_os`, applied after the
  file sync by `sets.py`.

Managing one app (e.g. OpenSSH) means editing two files in two directories: the
sync list in `applications/openssh.toml` and the `chmod 600` in `sets/40-*.toml`.
The split is arbitrary. A single file should describe everything about one
config: what to sync **and** the hooks/transforms around it.

## Goals

- One unified TOML format under `~/.mackup/applications/` (+ package stock).
- A file may declare any combination of: sync list, mapped files, action blocks.
- Blocks are first-class and richly conditioned (the emphasis of this work).
- Preserve current behavior for the 607 stock apps (sync-only) and for the
  existing sets (now expressed as blocks).

## Non-goals

- No change to the sync engine's two-way-by-mtime algorithm.
- No change to markers (definitions in `markers/`, state in XDG) or dconf.
- No new storage backends.

## Format (superset)

A config file `~/.mackup/applications/<name>.toml` composes three independent
parts. **Sync declaration and blocks are separate concerns:**
`configuration_files` / `[mapped_files]` are NOT blocks — they stay in
`[application]` and describe what to sync. Everything else is blocks.

**Parts:**

1. **Sync declaration** (optional) — `[application]` table with `name`,
   `configuration_files`, optional `source_env`, and an optional
   `[mapped_files]` table. Unchanged from today; never a block.
2. **Top-level implicit block** (optional) — action fields written at the top
   level ARE one block. The whole top level is parsed as a single block: if it
   has a `type`, it becomes block #0. This is the ergonomic common case
   ("this file is one chmod").
3. **Additional blocks** (optional) — a `[[block]]` array for more than one
   action.

```toml
# Case A — hybrid: sync .ssh, then chmod it (top-level implicit block)
[application]
name = "OpenSSH"
configuration_files = [".ssh"]

type = "chmod"          # <- top level IS a block
path = "~/.ssh"
recursive = true
dir_mode = "700"
file_mode = "600"
```

```toml
# Case B — one file, several actions (top-level block + [[block]] array)
type = "run"            # block #0 (top level)
phase = "pre"
require_command = ["theme-switcher"]
commands = ["theme-switcher schedule update", "theme-switcher apply"]

[[block]]               # block #1
type = "copy"
from = "~/.apps/${MACKUP_XDG_STATE}"
to = "~/.local/bin"
```

Degenerate shapes, all valid:

- **stock app** — only `[application]`, no block fields (the 607 unchanged).
- **pure hook** — only block fields (top level and/or `[[block]]`), no
  `[application]` (e.g. `10-enable-linger.toml`).
- **hybrid** — sync declaration + blocks (e.g. `openssh.toml` above).

`source_env` is a reserved sync/meta key (read from `[application]` or the top
level); it is never treated as a block field.

### Block schema

A block is either the top-level implicit block (top-level action fields, block
#0) or one entry in the `[[block]]` array. Both are parsed into the same block
shape; a single ordered list preserves declaration order (top-level block first,
then `[[block]]` entries in file order):

| field | meaning |
|---|---|
| `type` | `copy` \| `chmod` \| `run` \| `mutate_xml` \| `systemd_dropin` (required) |
| `phase` | `pre` \| `post` — relative to *this config's* file sync (default `post`) |
| `restart_service` | optional; bracket this block's action with user-service stop/start |

**Conditions** (all optional; list values are any-of; a block runs only if
*every* condition on that block passes — there is no separate file-level
inheritance, since the top level is itself a block):

| condition | passes when |
|---|---|
| `require_os` | current OS in list (`linux`/`macos`/`android`/`windows`) |
| `require_arch` | `platform.machine()` in list (`x86_64`/`aarch64`/…) |
| `require_marker` | every listed marker is set |
| `skip_if_marker` | none of the listed markers is set |
| `require_command` | every listed binary is on `PATH` |
| `require_gui` | `true` and `MACKUP_HAS_GUI` |
| `require_exists` | every listed path exists (after `~`/`$VAR` expansion) |
| `skip_if_exists` | none of the listed paths exists |
| `require_env` | list → every var set; table → every var equals given value |

**Type-specific fields** (unchanged from today's blocks):

- `copy`: `from`, `to` (file or directory; `~`/`$VAR`).
- `chmod`: `path`/`paths`, `mode` \| (`recursive` + `dir_mode`/`file_mode`).
- `run`: `script` \| `commands` (list), `shell`.
- `mutate_xml`: `files`/`files_mode`, `select`, `set_attr`, `set_child`,
  `create_missing`, `create_parents`.
- `systemd_dropin`: `service`, `name`, `Environment`, `MemoryMax`/`CPUQuota`/`Nice`.

`require_command` on `run` is subsumed by the generic `require_command`
condition.

## Execution model (`mackup sync`)

```
backup.d hooks
dconf dump                         (backup-role machine)
for config in sorted-by-filename(all configs):
    blocks = [top-level block if it has a type] + [[block]] entries   (file order)
    run blocks where phase == "pre"    (in order, each gated)
    sync config.configuration_files + mapped_files   (unless name-ignored)
    run blocks where phase == "post"   (in order, each gated)
dconf load                         (restore-role machine)
```

- **File sync** is gated only by `.mackup.cfg` `applications_to_ignore` /
  `applications_to_sync` (by config id), exactly as today.
- **A block runs** iff every condition on that block passes. There is no
  file-level condition inheritance — the top level is itself a block with its
  own conditions.
- **Ordering:** within a config — pre blocks (order) → file sync → post blocks
  (order); the top-level block precedes `[[block]]` entries. Across configs —
  filename sort. Cross-cutting hooks keep coarse ordering via numeric filename
  prefixes (`10-`, `40-`).
- **Locality note:** attaching a hook to the config whose files it touches (ssh
  perms in `openssh.toml`) removes cross-config ordering hazards, because the
  block runs immediately after that config's own files are synced. Standalone
  hooks that must run "after everything" use a late prefix (`zz-`).

## Migration

### Code

- Unify loading: one loader reads `applications/*.toml` producing, per config,
  (a) the sync mapping (today's `appsdb` output) and (b) the ordered block list
  with resolved conditions.
- `application.py` sync loop applies each config's pre-blocks, syncs, then
  post-blocks. The block engine (today's `sets.py` `[[run]]`/`[[copy]]`/
  `[[chmod]]`/`[[mutate_xml]]`/`[[systemd_dropin]]` + gating + service control)
  is reused, driven from the per-config loop instead of a separate directory
  pass.
- Condition evaluation centralized (one `block_passes(block, file_defaults)`
  helper) covering all conditions in the table above.

### Data

- Convert existing `~/.mackup/sets/*.toml` to the `[[block]]` format under
  `applications/`:
  - App-specific sets fold into their app file where natural (ssh perms →
    `openssh.toml`; syncthing tuning → a syncthing config).
  - Cross-cutting sets stay block-only files (`10-enable-linger.toml`,
    `50-apps-prebuilds.toml`, `60-theme-switcher.toml`).
- Remove the `~/.mackup/sets/` directory and `SETS_DIRNAME`.
- Stock 607 app definitions are untouched (only `[application]`).

### CLI

- `mackup apply-sets` → `mackup apply`: runs blocks only (pre + post) across all
  configs, no file sync — for re-applying hooks without a full sync.
- `mackup list` hides block-only configs (no `[application]`); `mackup show`
  additionally prints a config's blocks.

## Edge cases

- **Block-only config** (no `[application]`): participates in the sync loop for
  its blocks; not subject to name ignore/sync (nothing to sync); hidden from
  `list`.
- **A config both name-ignored and block-bearing:** file sync skipped, blocks
  still evaluated (blocks are gated by conditions, not by name).
- **`require_exists` for post-sync perms:** a chmod in the same file as the
  synced dir sees the freshly-synced path because it runs in that config's
  post phase.
- **dconf / backup.d** are unchanged and remain global (before/after the loop).

## Testing

- Loader: parses superset (all three parts; each degenerate shape); rejects
  malformed `type`.
- Conditions: unit tests per condition (os/arch/marker/command/gui/exists/env),
  file-level-default merge, any-of list semantics.
- Execution order: pre before sync before post; declaration order within phase;
  filename order across configs.
- Locality: chmod in an app file sees that app's just-synced files.
- Migration parity: each converted set reproduces its former effect.
- Regression: 607 stock apps still load and sync; `list`/`show` behavior.
