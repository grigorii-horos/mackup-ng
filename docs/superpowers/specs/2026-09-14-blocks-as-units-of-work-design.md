# Blocks as units of work

Date: 2026-09-14
Status: approved for planning

## Goal

Make a config an **ordered sequence of units of work** instead of "a file list
with blocks bolted to either end". A block may carry `files`, an action, or
both, and everything a block can be gated by — the existing `[when]`
vocabulary — becomes available to file lists for free.

The motivating problem: `~/Library` paths must be skipped on non-macOS
machines. A config-level `[when]` cannot express that for a config that syncs
both `~/Library` and XDG paths, because it gates the whole file. The previous
attempt split 120 such configs into `<app>.toml` + `<app>-macos.toml`, which
doubles the application ids and scatters one application across two files.

The rejected alternative was a second array-of-tables (`[[sync]]`) beside
`[[block]]`. It was rejected for the right reason: two mechanisms for one idea.
A block is already "conditions plus something to do"; syncing files is
something to do.

## Non-goals

- No change to the `[when]` condition vocabulary beyond adding `not_os`.
- No change to global destination ownership. Which config wins a contested
  destination is still decided across all configs in read order.
- No global (cross-config) stages. `pre` and `post` remain scoped to their own
  config, as they are today.
- No change to path templating, brace expansion or platform selectors.

## The model

A config that passes its top-level `[when]` executes as:

```
1. blocks with phase = "pre"      in declaration order
2. the top-level unit             top-level files + mapped_files, then the
                                  top-level action if one is present
3. blocks with phase = "during"   declaration order; the default phase
4. blocks with phase = "post"     in declaration order
```

Within a unit: **files first, then the action.** A unit may have only files,
only an action, or both. A config whose top-level `[when]` fails declares
nothing — no pairs, no blocks — exactly as today.

The top-level is not a special case in the executor: it is one unit like any
other, assembled from the keys at the top of the file, sitting between the
`pre` blocks and the `during` blocks. This is what "global parameters are an
unfolded block" means in code.

**Slot numbering.** Units are numbered 0, 1, 2 … in final execution order —
across all three phases, not within one. So a config with one `pre` block
takes slot 0 for it, slot 1 for the top-level unit, and slots 2… for its
`during` and `post` blocks in that order. Ordering the sync loop is then a
plain sort by slot, with no phase logic at execution time; phases exist only
to decide the numbering when the config is parsed.

## Config syntax

```toml
name = "SSH"
files = [".ssh"]

# A block with only files: a conditional file section.
[[block]]
files = ["Library/Preferences/ssh"]
[block.when]
os = "macos"

# A block with only an action: unchanged from today.
[[block]]
[block.chmod]
path = "~/.ssh"
recursive = true
dir_mode = "700"
file_mode = "600"

# A block with both: sync these paths, then act on them.
[[block]]
files = [".ssh/config.d"]
[block.when]
not_os = "macos"
[block.chmod]
path = "~/.ssh/config.d"
file_mode = "600"
```

### `phase`

| Value | Meaning |
| --- | --- |
| `"pre"` | before this config's top-level unit |
| `"during"` | in declaration order after the top-level unit — **the default** |
| `"post"` | after every other unit of this config |

The default changes from `"post"` to `"during"`. See Compatibility.

### `not_os`

`conditions.py` already pairs `marker` with `not_marker` and `exists` with
`not_exists`. `not_os` completes the row for `os`:

```python
if key == "not_os":
    return hooks.os_kind() not in _as_list(value)
```

added to `CONDITION_KEYS`. `not_arch` is not added — nothing needs it, and
`command` and `gui` have no negative forms either.

`not_os` is what the migrated configs use for their non-macOS section, so that
a machine reporting `android` (which `hooks.os_kind()` returns separately from
`linux`) keeps its XDG paths.

## Implementation

### The slot

`mapping.Pair` gains `owner_slot: int`: the index of the unit that declared the
pair within its config. `owner_app` keeps its current meaning.

`ApplicationsDatabase` builds, per config, an ordered list of units. Each unit
records its `when` table, its file mappings and its action block, if any. A
unit's `when` is evaluated once, when the database is built, and the outcome is
stored on the unit as `passed`. Conditions describe the machine — OS,
architecture, markers, installed commands, existing paths — and a run does not
change those, which is already the assumption behind `config_enabled`. One
caveat worth naming: a `[run]` action that installs a command cannot make a
later unit's `command` condition start passing within the same run.

Only pairs from units whose `when` passes enter `configuration_files` and
`app_file_mappings`. Global ownership resolution therefore needs no change: it
still receives a flat list of pairs and still resolves in read order, but each
pair now knows which slot it came from.

### API changes on `ApplicationsDatabase`

| Method | Now | After |
| --- | --- | --- |
| `get_file_mappings(name)` | `list[tuple[str, str]]` | `list[tuple[str, str, int]]` — `(local, backup, slot)` |
| `get_blocks(name)` | `list[dict]` | unchanged — the action blocks in execution order, still used by `show` and `apply` |
| `get_units(name)` | — | new, and the primary API for the sync loop: the ordered units, each with `slot`, `when`, `passed`, `mappings` and `block` |
| `get_files(name)` | unchanged | unchanged — still the flat list of enabled local paths |
| `app_has_sync(name)` | unchanged | unchanged — true when any enabled unit declares files |

`get_file_mappings` has four call sites (`main.py:150`, `main.py:262`,
`main.py:379`, `info.py:168`); each unpacks two values today and must unpack
three.

### The sync loop

`main.py` currently builds `groups_by_owner: dict[str, list[(source, dests)]]`
and, per config, runs `pre` blocks, then every group, then `post` blocks.

It becomes `groups_by_slot: dict[tuple[str, int], list[(source, dests)]]`, and
the per-config loop walks the config's units in order: for each unit, sync the
groups belonging to `(app_name, slot)`, then apply that unit's action.

### `restart_service`

`blocks.apply_blocks` defers a service start across the blocks of one phase, so
three blocks that all declare `restart_service = "syncthing"` produce one stop
and one start. With units applied one at a time, that deferral must be held for
the **whole config** instead of one phase, or the same three blocks would
produce three restarts. The user's `30-syncthing-low-resource.toml` relies on
this and says so in a comment.

### `mackup apply`

`apply` is documented as running action blocks **without syncing files**. That
contract is kept: `apply` walks the same unit order and runs actions only,
skipping every unit's file set. A block that carries only files is a no-op
under `apply`.

### `mackup show`

A unit skipped by its `when` currently becomes invisible: its paths simply are
not in the list. A config-level `[when]` failure, by contrast, prints
`conditions not met on this machine (os=macos)`. `show` must report skipped
units the same way — naming the slot and the failing condition — so a
condition-gated section is never silently absent.

## Compatibility

Measured, not assumed:

- **Shipped configs use blocks zero times.** Every block in play lives in the
  author's own `~/.config/mackup/applications/`: 19 blocks across 12 configs.
- **No block anywhere declares `phase` explicitly.** All 19 run at the current
  default, `post`.
- Of those 12 configs, only three declare both files and blocks: `openssh`
  (1 file, a `chmod`), `termux` (2 files plus `mapped_files`, a `chmod`), and
  `zz-my-files` (88 files, one block). All three want their action to run
  *after* their files, and each says so in a comment.

Under the new order the top-level files sync before any `during` block, so all
three keep their behaviour with no edit. Changing the default from `post` to
`during` is therefore behaviour-preserving for every block that exists.

An explicit `phase = "post"` keeps working and still means "after everything
else in this config".

## Migration of the shipped configs

The previous change split 120 configs that mix `~/Library` with other paths
into `<app>.toml` + `<app>-macos.toml`. That is reversed:

- the pair is merged back into one `<app>.toml`;
- the non-macOS paths become a block gated `not_os = "macos"`;
- the `~/Library` paths become a block gated `os = "macos"`;
- the 120 `-macos.toml` files are deleted.

The 136 configs whose paths are *all* under `~/Library` keep their
config-level `[when] os = "macos"`. Gating the whole file is the correct shape
when the whole file is macOS-only, and it is what makes `show` report the
config as skipped.

## Error handling

| Situation | Behaviour |
| --- | --- |
| `phase` outside `pre`/`during`/`post` | Warning naming the config and the value; the block is treated as `during` |
| `[[block]]` with neither `files` nor an action | Warning naming the config; the block is skipped |
| `files` in a block is not a list of strings | Warning naming the config; the block's file set is ignored, its action still runs |
| Unrecognized `[when]` key | Warning, as today (`conditions.unrecognized_keys`) |
| A unit's `when` fails | The unit contributes no pairs and its action does not run; `show` reports it |

Warnings follow the established pattern, `print(utils.colorize_message("Warning: ..."))`.

## Testing

- `not_os` evaluates the complement of `os`, including for `android`.
- Unit order: a config with `pre`, top-level files, two `during` blocks and a
  `post` block executes in the documented order. Asserted on recorded effects,
  not on mocks.
- Files-first-then-action within one unit: a block that syncs a file and then
  chmods it observes the synced file.
- A block gated `os = "macos"` contributes no pairs on Linux, and its action
  does not run.
- A pair's `owner_slot` survives ownership resolution: when two configs claim
  one destination, the winner's slot is the one used for ordering.
- `restart_service` across three blocks of one config yields one stop and one
  start.
- `mackup apply` runs a mixed block's action and does not sync its files.
- `mackup show` names a unit skipped by its condition.
- Existing behaviour: a block with no `phase` runs after the top-level files.
- Invariant (replacing the current one): no shipped config contains a
  `~/Library` path outside a unit gated to macOS, and no `<app>-macos.toml`
  remains.

## Risks

- **The sync loop is the riskiest edit.** Ordering, ownership and the deletion
  tombstone pass all meet in `main.py`'s one loop. The slot key threads through
  it; a mistake shows up as files synced in the wrong order or, worse, silently
  not synced. The unit-order tests exist for this.
- **`get_file_mappings` changes arity**, touching four call sites including
  `info.py`. A missed site is a runtime unpack error rather than a silent
  fault, which is the good failure mode.
- **120 configs are rewritten mechanically.** The risk is a translation error,
  not a design flaw; the invariant test is the check.

## Findings from the final review

Two consequences of the model above were not spelled out when this spec was
written; the final review established both and neither changes any code.

- **A path a failing unit no longer declares is silently left alone.** When a
  unit's `[when]` does not hold, its paths never enter the pair list at all —
  not as a pair, not as an eviction. The deletion-tombstone pass only ever
  sees paths that were once in the pair list, so it never considers this one:
  locally and in the store, the file simply stays untouched. This is the
  correct outcome for the `~/Library` case (the backup belongs to a machine
  this one is not) — a unit-gated path is not this machine's to delete — but
  nothing in the spec said so before now.
- **The verbose orphan report does not cover unit-gated paths.** `mackup sync
  -v` prints `… has no destination, left untouched` for a backup path whose
  *config-level* `[when]` failed (see `main.py`'s sync branch, which walks
  `app_db.get_file_mappings` for every enabled-but-gated-out config), but a
  path whose *unit-level* `[when]` failed never reaches that report — it was
  never a candidate pair in the first place, at any point, so there is
  nothing to compare it against. Before the config merge, when `~/Library`
  paths and their siblings lived in separate `<app>.toml` / `<app>-macos.toml`
  files, a `~/Library` path on non-macOS came from a *config* gated out by
  `[when]` and so was reported. After the merge it comes from a *unit* gated
  out by `[block.when]` inside one still-enabled config, and is not. This is
  a real behaviour change introduced by that merge, not a bug in this task —
  documented here rather than fixed, per the review.
