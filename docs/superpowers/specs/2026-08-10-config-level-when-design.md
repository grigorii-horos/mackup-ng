# Config-level `[when]`: conditions that gate a whole config, sync list included

**Date:** 2026-08-10
**Status:** approved design, pending implementation plan

## Problem

Conditions (`os`, `marker`, `not_marker`, `command`, `gui`, `exists`, `env`, …)
currently gate action blocks only. A config's `files` and `[mapped_files]`
always apply, on every machine.

That leaves machine-specific file content with no home in the mapping model.
The concrete case is Termux colors. Two configs copy a palette over the same
local file:

```toml
# 76-termux-colors.toml — default
[when]
os = ["android"]
[copy]
from = "$MACKUP_BACKUP_DIR/.termux/Neutral.properties"
to = "~/.termux/colors.properties"
```

```toml
# 77-termux-colors-eink.toml — e-ink machines
[when]
os = ["android"]
marker = ["eink"]
[copy]
from = "$MACKUP_BACKUP_DIR/.termux/colors-eink.properties"
to = "~/.termux/colors.properties"
```

while `termux.toml` also lists `.termux/colors.properties` in `files`. The
generated local file is therefore synced back into the shared backup: on an
e-ink machine the e-ink palette lands in the common
`.termux/colors.properties` and reaches every other machine, where only the
re-run of the copy block papers over it. The mechanism works by ordering luck
and pollutes the backup.

Since 2.0.0 the model expresses exactly this shape — one destination, several
candidate sources, later config wins — but only if a config can decline to
declare its mappings on a machine where they do not apply.

## Goals

- A config can be gated as a whole by the existing condition vocabulary.
- Machine-specific mappings become ordinary mappings, resolved by the same
  destination-keyed rules as everything else.
- Retire the copy-block workaround for Termux colors.

## Non-goals

- No new condition keys; `conditions.py`'s vocabulary is unchanged.
- No per-entry conditions inside `files` / `[mapped_files]`.
- No one-way mappings. A gated mapping syncs bidirectionally like any other.

## Semantics

A top-level `[when]` table is the **config's** condition. When it does not
pass, the config contributes nothing: no sync pairs, and no blocks — neither
the implicit top-level block nor any entry of the `[[block]]` array.

`when` becomes a reserved top-level key, alongside `name`, `files`,
`configuration_files`, `mapped_files`, `source_env`, `block` and
`application`. It therefore no longer becomes part of the implicit top-level
block.

A condition that should gate one action rather than the whole config lives in
that block, which already works today:

```toml
[[block]]
[block.when]
os = ["linux"]
[block.chmod]
path = "~/.ssh"
```

### Effect on the plan

A gated-out config declares no destinations, so its pairs never enter
resolution and never evict anyone. If an e-ink override is gated out, the
destination stays with the base config read earlier; if the base config is
gated out instead, the override takes the destination.

A backup source left without destinations because its config was gated out is
an ordinary orphan: untouched on disk, reported by `sync -v`. Nothing is
deleted.

`mackup apply` honours the same gate. `mackup list` still lists the config;
`mackup show` prints a line stating that the config's conditions are not met
on this machine.

### Compatibility

Configs that pair a top-level `[when]` with an action but declare no files are
unaffected — gating the config and gating its only block are the same thing.
No stock config uses a top-level `[when]`.

The one config whose behavior changes is a config that combines a top-level
`[when]` with `files`: its file list becomes conditional. The migration note
in the docs must say so plainly, and point at `[[block]]` + `[block.when]` for
authors who meant to gate only the action.

## Termux configs

`76-termux-colors.toml` and `77-termux-colors-eink.toml` are deleted. The
palette becomes a mapping, and because resolution follows the alphabetical
read order, the override must sort after the base config — hence the rename.

`termux.toml`:

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

[[block]]
[block.chmod]
path = "~/.termux/boot"
recursive = true
file_mode = "+x"
```

`zz-termux-colors-eink.toml`:

```toml
[when]
os = ["android"]
marker = ["eink"]

[mapped_files]
".termux/colors.properties" = ".termux/colors-eink.properties"
```

On an e-ink machine the destination resolves to `colors-eink.properties`; on
any other Android machine to `Neutral.properties`. The sync is bidirectional,
so editing the palette on the phone updates its source and reaches the other
machines of the same style. The now-unused `.termux/colors.properties` in the
backup folder should be removed by hand, or it lingers as an orphan.

These files live in the user's `~/.mackup/applications/`, not in the
repository; the repository change is the feature plus its documentation.

## Implementation

- `conditions.py` gains `config_passes(data)` — the same evaluation as
  `block_passes`, reading the config's top-level `when` table. `block_passes`
  is unchanged.
- `appsdb.py` adds `when` to the reserved keys so it stays out of the implicit
  top-level block, and records each config's condition table.
- The gate is applied where configs are consumed: building the sync plan in
  `main.py`, the per-config block execution in `sync`, and `apply`.
- `show` reports the unmet condition; `list` is unchanged.

## Testing

- A config whose `[when]` fails contributes no pairs and runs no blocks,
  including entries of its `[[block]]` array.
- A config whose `[when]` passes behaves exactly as before.
- Marker-gated override: with the marker set, the destination resolves to the
  override's source; without it, to the base config's source; toggling the
  marker flips it back.
- A source orphaned by a gated-out config is untouched and reported by
  `sync -v`.
- `apply` skips a gated-out config's blocks.
- A block-only config with a top-level `[when]` keeps its current behavior.
