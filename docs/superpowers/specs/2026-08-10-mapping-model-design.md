# Mapping model: destination-keyed pairs, fanout groups, deterministic override

**Date:** 2026-08-10
**Status:** approved design, pending implementation plan

## Problem

Today a managed item is a `(local, backup)` pair collected per application into
a `set`, and the sync engine treats each pair independently. Two properties of
that model cause silent, unpredictable behavior:

1. **No uniqueness rule.** The same backup path may appear in several pairs, and
   so may the same local path. Nothing detects or resolves it.
2. **No order.** `ApplicationsDatabase.get_config_files()` returns a `set`,
   `configuration_files` and the mappings are `set`s, so which entry is seen
   first is an implementation detail of the hash seed.

Concrete failure: two local files mapped to one backup file. Sync visits pair A,
sees the local side newer, writes the backup. Sync then visits pair B, sees the
backup newer, overwrites B's local file with A's content. The user never asked
for that, and which file wins depends on iteration order.

At the same time, both many-to-one shapes are legitimately wanted:

- one backup file distributed to several local destinations (fanout);
- one local file whose content comes from a different backup entry than the
  stock config declares (override).

## Goals

- A destination-keyed model where "which backup feeds this local file" always
  has exactly one answer.
- First-class fanout: one backup source may feed N local destinations.
- Deterministic, documented precedence so a later config can override an earlier
  one on purpose.
- No TOML syntax change; existing configs keep working.

## Non-goals

- No change to storage backends, markers, dconf, hooks or action blocks.
- No new CLI commands (`show` and `sync -v` are extended instead).
- No conflict-resolution UI; mtime stays the arbiter.

## Model

The unit is a **pair** `(source_backup, dest_local, owner_app)`, held in an
ordered list, not a set.

**Uniqueness rule:** `dest_local` is the key. When a new pair repeats a
`dest_local` already in the list, the previous pair is removed and the new pair
is appended at the end. `source_backup` may repeat freely — repetition is what
expresses fanout.

Resolution is **global**, across all applications selected for sync, not
per-application: an override may come from a different config file than the pair
it displaces.

### Read order

Pairs are appended in this order (later wins):

1. stock `mackup_ng/applications/*.toml`, alphabetical by app id;
2. `$XDG_CONFIG_HOME/mackup/applications/*.toml`, alphabetical;
3. `~/.mackup/applications/*.toml`, alphabetical.

A same-named custom file still excludes the file below it entirely (a custom
`firefox.toml` means the stock `firefox.toml` is not read at all) — unchanged
from today.

Within one file: `files` entries in declaration order, then `[mapped_files]`
entries in declaration order. Brace expansion inside a single entry expands in
alphabetical order, so a multi-path entry is itself deterministic.

### Derived structures

Resolution produces:

- `pairs: list[Pair]` — the final array;
- `groups: dict[source_backup, list[dest_local]]` — fanout groups;
- `evictions: list[(evicted_pair, winning_pair)]` — for reporting;
- `orphans: list[source_backup]` — sources left with zero destinations.

### Syntax

Unchanged. `files` yields identity pairs (`local == backup`). `[mapped_files]`
yields explicit pairs; its TOML keys are the local destinations, so destination
uniqueness holds by construction within a file, and several keys sharing one
value express fanout:

```toml
[mapped_files]
".config/app/work.profile/user.js"     = ".config/app/profile/user.js"
".config/app/personal.profile/user.js" = ".config/app/profile/user.js"
```

## Sync engine

A group is one `source_backup` plus all its `dest_local` paths. **All members
are equal peers** — the backup side has no special authority.

**Files.** Compute the effective mtime of every member that exists; the newest
wins and is copied to every member with an older mtime, backup included. If no
member exists, the group is a no-op. Members that are the same inode
(`os.path.samefile`) are skipped.

**Directories.** Per-entry union across the whole group. For each relative path
inside the trees, collect the members where it exists, let the newest win, and
propagate it to the other members; a path missing from a member is created
there. All members converge to the union of their contents.

**1:1 is the same code path** — a group of two members — so today's behavior for
ordinary configs is preserved.

Because a group can span applications, the plan is built once in `Mackup` and
executed against groups; statistics are attributed to the `owner_app` of the
winning pair. Action blocks stay per-application and keep their current order.

## Orphaned sources

A backup file whose pairs were all evicted keeps its content and is left
untouched — it is simply not synced on this machine, and still works on a
machine without the override. It is reported under `sync -v`.

## Removal

`mackup rm <path>` operates on the **destination**:

- delete the local file at `dest`;
- record a tombstone for `dest` in `.mackup-deletions` (format unchanged);
- delete the backup source only if no live destination remains for it;
- otherwise print how many destinations still feed from that source.

During sync, pairs whose `dest` carries a tombstone are dropped before grouping,
so the removed copy does not come back on another machine while the rest of the
group keeps syncing. When tombstones consume every destination of a source, the
source is deleted, since the removal was explicit.

## Diagnostics

- `sync -v` prints, after resolution: each eviction
  (`.config/x <- .config/x (app-a) evicted by zz-work.toml`) and each orphaned
  source (`.config/x has no destination, left untouched`).
- `show <application>` prints the application's final pairs — source,
  destination, `fanout: N destinations` — and marks entries the application
  declares that lost to another config.
- Without `-v`, output is unchanged apart from the existing statistics.

## Code structure

New module `src/mackup_ng/mapping.py` holds the whole model as pure functions
over data, with no filesystem access:

- `Pair(source, dest, owner_app)`;
- `build_pairs(entries) -> (pairs, evictions)` — the last-wins rule;
- `group_by_source(pairs) -> (groups, orphans)`.

Changed modules:

- `appsdb.py` — ordered lists instead of sets; `get_config_files()` returns a
  list in precedence order; per-app deduplication removed (the resolver owns it).
- `mackup.py` — builds the plan once for all selected applications and drives
  group execution.
- `application.py` — `sync_files()` (pairwise) becomes `sync_group(source,
  dests)`; `sync_directory_entries` generalizes from two members to N.

## Compatibility

TOML syntax is unchanged and 1:1 configs behave exactly as before. Behavior
changes in two places, both intentional:

- configs where two local paths already shared one backup path — previously
  silent cross-writing through the backup, now an explicit group with union
  semantics;
- the winner of a duplicate destination is now decided by documented read order
  instead of set iteration order.

## Testing

- read order is deterministic and matches the documented precedence;
- eviction within one file and across applications;
- a custom config beats a stock one;
- fanout file: newest member wins and reaches every other member;
- fanout directory: per-entry union across N members;
- an orphaned source is left untouched and reported under `-v`;
- `rm` on one destination leaves sibling destinations and the source intact;
- a tombstoned destination stays removed on a second machine while the group
  keeps syncing.
