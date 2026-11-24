# Deletion tracking (replaces `mackup rm`)

## Problem

`mackup sync` is a union merge: a directory group ends up with every entry any
member holds, and a single-file group takes the newest member. Absence carries
no meaning, so a file deleted on machine A comes straight back from machine B.
The only way to delete was `mackup rm <path>`, which had to be remembered and
run by hand.

## Model

Three-way sync per file, the way unison does it:

- **Snapshot** (machine-local, `$XDG_STATE_HOME/mackup/sync-snapshot.json`,
  never synced): for every file of every group the last sync touched, its
  fingerprint `[size, int(mtime)]`, keyed `home:<path>` or `backup:<path>`.
  It is the common base: what this machine saw when it last synced.
- **Tombstones** (synced, `<backup>/.mackup-tombstones/<host>.json`): backup
  path -> deletion time. Each machine writes only its own file, so two
  machines never edit the same file. Readers take the union, newest wins.
  Entries older than 90 days are dropped when the owning host rewrites its file.
- **Trash** (synced, `<backup>/.mackup-trash/<stamp>-<host>/{backup,home}/...`):
  nothing is ever unlinked; every deletion is a move into the trash.

Tracking is per file (directories are containers). A tombstone key is the
backup path of the file, which is the namespace shared by every machine.

## Rules (per file `K` in a group, before the file sync)

1. **Deleted here.** A local member had `K` in the snapshot and now lacks it,
   and every member that still holds `K` is unchanged since the snapshot:
   move those copies to the trash and tombstone `K`. Any change anywhere wins
   over the deletion: the normal sync then restores the file.
2. **Deleted elsewhere.** The backup lacks `K`, a tombstone for `K` exists, and
   every local copy is stale: unchanged since the snapshot, or — with no
   snapshot entry — neither its mtime nor its ctime is newer than the
   tombstone, so a copy restored with an old mtime (`cp -p`, an archive)
   still counts as new. It must predate the tombstone by an hour, since the
   tombstone carries another machine's clock (under `init`, the backup is
   authoritative and any local copy is stale). Move the local copies to the
   trash. A tombstone does nothing while the backup holds `K`, so a file
   re-created anywhere simply wins and nothing needs clearing.
3. **Otherwise** the ordinary merge runs, as before.

Absence from the backup without a tombstone never deletes anything: it may be
a backend that has not finished downloading.

## Safety

- No snapshot (first run, new machine, reinstall): rule 1 cannot fire.
- A local member whose parent directory is missing is "unavailable" (e.g. a
  Firefox profile directory that was removed): it neither reports a deletion
  nor gets one.
- More than 20 files to delete in one run: ask; `--force` says yes,
  `--force-no` or a non-interactive run says no and aborts the sync before any
  file is touched.
- Empty parent directories left by a deletion are pruned up to the member
  root's parent.
- A dry run plans and prints, but writes no tombstone, trash or snapshot.

## Migration

`mackup rm` is removed. Its `.mackup-deletions` log is read on every sync as
extra tombstones: each line (a destination, or a path below one) is mapped to
its backup path and dated by the log's mtime (the log has no per-line times).
The log is never written or deleted, so machines still on an older version
keep honoring it, and an aborted sync has written nothing. A legacy entry on a
fanout destination whose siblings were kept no longer excludes that
destination: it is synced again. Likewise a file an application re-created
after the `mackup rm` is now synced instead of being kept out — unless it
predates the log's mtime (the *last* removal), in which case it is moved to
the trash. The old version deleted such files outright, on every sync.
