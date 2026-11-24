"""Deletion tracking: tell "deleted here" from "never arrived here".

The sync engine is a union merge, so on its own it cannot see a deletion: a
file removed on one machine is simply copied back from another. This module
adds the missing memory, unison-style:

- the **snapshot** (machine-local, never synced) is what this machine saw at
  its last sync: one fingerprint per file of every group it synced;
- **tombstones** (in the backup folder, one file per host) say which backup
  paths were deleted, and when;
- the **trash** (in the backup folder) receives every deleted file: nothing is
  ever unlinked outright.

Before the file sync, :func:`plan_group` compares each group's files against
the snapshot and the tombstones and decides which copies to delete;
:func:`execute` moves them to the trash. After the sync,
:func:`refresh_snapshot` records the new common state.

Tracking is per file. Directories are containers: they are pruned once a
deletion leaves them empty. See
``docs/superpowers/specs/2026-11-24-deletion-tracking-design.md``.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from . import dirs, ignore, utils

if TYPE_CHECKING:
    from collections.abc import Callable

SNAPSHOT_FILENAME: str = "sync-snapshot.json"
TOMBSTONES_DIRNAME: str = ".mackup-tombstones"
TRASH_DIRNAME: str = ".mackup-trash"
# The explicit-removal log `mackup rm` used to write. Read as tombstones,
# never written or deleted.
LEGACY_DELETIONS_FILENAME: str = ".mackup-deletions"
LEGACY_HOST: str = "mackup rm (legacy)"

# A tombstone only has to outlive the slowest machine's next sync.
TOMBSTONE_TTL: float = 90 * 24 * 3600
# More files than this in one run, and the sync asks before deleting any.
CONFIRM_THRESHOLD: int = 20

# fingerprint() of a path that exists but is not a regular file.
OTHER: str = "other"
# A fingerprint is [size, mtime].
FINGERPRINT_LEN: int = 2

BACKUP: str = "backup"
HOME: str = "home"


def host_name() -> str:
    """This machine's name, safe to use as a file name."""
    name = re.sub(r"[^A-Za-z0-9._-]", "_", socket.gethostname())
    return name.strip(".") or "unknown"


def fingerprint(path: str) -> list[int] | str | None:
    """``[size, mtime]`` of a regular file, :data:`OTHER`, or None if absent.

    The mtime is truncated to whole seconds: sync backends and filesystems
    (FAT, Android storage) do not all keep sub-second precision, and a
    fingerprint that changes on its own would only ever block a deletion.
    """
    if not os.path.lexists(path):
        return None
    if os.path.isdir(path) or not os.path.isfile(path):
        return OTHER
    stat = os.stat(path)
    return [stat.st_size, int(stat.st_mtime)]


def _join(root: str, rel: str) -> str:
    return os.path.join(root, rel) if rel else root


def _ignored(rel: str, globs: ignore.Globs) -> bool:
    return bool(rel) and any(
        ignore.is_ignored(globs, part) for part in rel.split(os.sep)
    )


def member_files(root: str, globs: ignore.Globs = ()) -> set[str]:
    """Files of one group member, relative to it; ``""`` for a file member."""
    if os.path.isdir(root):
        found: set[str] = set()
        for cur, subdirs, files in os.walk(root):
            subdirs[:] = [d for d in subdirs if not ignore.is_ignored(globs, d)]
            for name in files:
                if not ignore.is_ignored(globs, name):
                    found.add(os.path.relpath(os.path.join(cur, name), root))
        return found
    if os.path.lexists(root):
        return {""}
    return set()


# --- snapshot -----------------------------------------------------------------


def snapshot_path() -> str:
    """The machine-local snapshot, under ``$XDG_STATE_HOME/mackup``."""
    return os.path.join(dirs.state_dir(), SNAPSHOT_FILENAME)


def load_snapshot() -> dict[str, list[int]]:
    """The last sync's fingerprints. Missing or corrupt means no snapshot."""
    try:
        with open(snapshot_path(), encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        str(key): value
        for key, value in data.items()
        if isinstance(value, list) and len(value) == FINGERPRINT_LEN
    }


def save_snapshot(snapshot: dict[str, list[int]]) -> None:
    """Write the snapshot atomically. A snapshot we cannot write is no error:
    the next run simply detects no deletions, as on a first run."""
    path = snapshot_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(snapshot, handle, sort_keys=True)
        os.replace(tmp, path)
    except OSError:
        return


def _snapshot_key(side: str, rel: str) -> str:
    return f"{side}:{rel}"


def _snapshot_rels(snapshot: dict, side: str, root: str) -> set[str]:
    prefix = _snapshot_key(side, root)
    below = prefix + os.sep
    rels: set[str] = set()
    for key in snapshot:
        if key == prefix:
            rels.add("")
        elif key.startswith(below):
            rels.add(key[len(below) :])
    return rels


# --- tombstones ---------------------------------------------------------------


def tombstones_dir(backup_root: str) -> str:
    """Where every host keeps its own tombstone file."""
    return os.path.join(backup_root, TOMBSTONES_DIRNAME)


def _read_json_dict(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def load_tombstones(backup_root: str) -> dict[str, dict]:
    """Every host's tombstones: backup path -> ``{"ts", "host"}``, newest wins."""
    folder = tombstones_dir(backup_root)
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return {}
    merged: dict[str, dict] = {}
    for name in names:
        if not name.endswith(".json"):
            continue
        host = name[: -len(".json")]
        for raw_key, ts in _read_json_dict(os.path.join(folder, name)).items():
            if not isinstance(ts, (int, float)):
                continue
            key = os.path.normpath(raw_key)
            if key not in merged or ts > merged[key]["ts"]:
                merged[key] = {"ts": float(ts), "host": host}
    return merged


def record_tombstones(
    backup_root: str,
    keys: list[str],
    now: float | None = None,
) -> None:
    """Add ``keys`` to this host's tombstone file, dropping expired entries."""
    now = time.time() if now is None else now
    path = os.path.join(tombstones_dir(backup_root), f"{host_name()}.json")
    own = {
        key: ts
        for key, ts in _read_json_dict(path).items()
        if isinstance(ts, (int, float)) and ts >= now - TOMBSTONE_TTL
    }
    for key in keys:
        own[os.path.normpath(key)] = now
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(own, handle, indent=1, sort_keys=True)
    os.replace(tmp, path)


def tombstone_for(key: str, tombstones: dict[str, dict]) -> dict | None:
    """The tombstone covering ``key``: its own, or a deleted ancestor's."""
    path = os.path.normpath(key)
    while path and path not in (os.sep, os.curdir):
        if path in tombstones:
            return tombstones[path]
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    return None


# --- planning -----------------------------------------------------------------


@dataclass(frozen=True)
class Member:
    """One member of a fanout group: the backup source or a destination."""

    side: str  # BACKUP or HOME
    rel: str  # path relative to its base folder
    path: str  # absolute path


@dataclass(frozen=True)
class Victim:
    """One copy of a deleted file, to be moved to the trash."""

    path: str  # absolute
    stop: str  # pruning of emptied parents stops at (keeps) this directory
    trash_rel: str  # where it goes inside one trash run


@dataclass
class Deletion:
    """One file deleted across its group."""

    key: str  # backup path, the name every machine shares
    here: bool  # deleted on this machine: record a tombstone
    victims: list[Victim] = field(default_factory=list)
    host: str = ""  # for a remote deletion: who deleted it


def group_members(
    backup_root: str,
    home: str,
    source: str,
    dests: list[str],
) -> list[Member]:
    """The backup source first, then every destination."""
    source = os.path.normpath(source)
    members = [Member(BACKUP, source, os.path.join(backup_root, source))]
    for raw_dest in dests:
        dest = os.path.normpath(raw_dest)
        members.append(Member(HOME, dest, os.path.join(home, dest)))
    return members


def plan_group(  # noqa: PLR0913
    backup_root: str,
    home: str,
    source: str,
    dests: list[str],
    *,
    snapshot: dict[str, list[int]],
    tombstones: dict[str, dict],
    globs: ignore.Globs = (),
    prefer_backup: bool = False,
) -> list[Deletion]:
    """Decide which files of one group were deleted, and where to delete them.

    A file deleted on this machine (in the snapshot, now missing locally) is
    deleted everywhere — unless some copy changed since the snapshot, because
    an edit always beats a deletion. A file the backup lacks and a tombstone
    covers is deleted locally, but only where the local copy is stale.
    """
    members = group_members(backup_root, home, source, dests)
    backup = members[0]
    # A destination whose parent directory is gone (a removed Firefox profile,
    # say) is unavailable, not deleted: it neither reports nor gets a deletion.
    available = [member for member in members if _available(member)]

    rels: set[str] = set()
    for member in available:
        rels |= member_files(member.path, globs)
        rels |= _snapshot_rels(snapshot, member.side, member.rel)
    rels = {rel for rel in rels if not _ignored(rel, globs)}

    plan: list[Deletion] = []
    for rel in sorted(rels):
        key = _join(backup.rel, rel)
        state = {m: fingerprint(_join(m.path, rel)) for m in available}
        prev = {
            m: snapshot.get(_snapshot_key(m.side, _join(m.rel, rel)))
            for m in available
        }
        present = [m for m in available if state[m] is not None]
        if not present:
            continue

        deleted_here = any(
            m.side == HOME and prev[m] is not None and state[m] is None
            for m in available
        )
        if deleted_here:
            if all(state[m] == prev[m] for m in present):
                plan.append(Deletion(key, True, _victims(present, rel)))
            continue

        if state[backup] is not None:
            continue
        tombstone = tombstone_for(key, tombstones)
        if tombstone is None:
            # Missing from the backup without a tombstone: maybe not downloaded
            # yet. Never a reason to delete — the ordinary sync restores it.
            continue

        if all(
            _stale(
                _join(m.path, rel),
                state[m],
                prev[m],
                tombstone["ts"],
                prefer_backup=prefer_backup,
            )
            for m in present
        ):
            plan.append(
                Deletion(key, False, _victims(present, rel), tombstone["host"]),
            )
    return plan


def _available(member: Member) -> bool:
    """The backup always is; a destination is when its parent directory exists."""
    return member.side == BACKUP or os.path.isdir(os.path.dirname(member.path))


def _stale(
    path: str,
    current: list[int] | str | None,
    previous: list[int] | None,
    deleted_at: float,
    *,
    prefer_backup: bool,
) -> bool:
    """Whether a local copy of a file deleted elsewhere may go too.

    It may when it is unchanged since this machine's last sync. With no
    snapshot entry, it may when nothing about it changed after the deletion:
    the inode change time counts as well as the mtime, so a file restored with
    an old mtime (``cp -p``, an archive) is still recognized as new. Under
    ``prefer_backup`` the backup is authoritative and any copy may go.
    """
    if previous is not None:
        return current == previous
    if current == OTHER:
        return False
    if prefer_backup:
        return True
    stat = os.stat(path)
    return max(stat.st_mtime, stat.st_ctime) <= deleted_at


def _victims(members: list[Member], rel: str) -> list[Victim]:
    return [
        Victim(
            path=_join(m.path, rel),
            stop=os.path.dirname(m.path),
            trash_rel=os.path.join(m.side, _join(m.rel, rel)),
        )
        for m in members
    ]


def victim_count(plan: list[Deletion]) -> int:
    """How many files the plan deletes."""
    return sum(len(deletion.victims) for deletion in plan)


# --- execution ----------------------------------------------------------------


def trash_run_dir(backup_root: str, now: float) -> str:
    """The trash folder of one run: a timestamp plus this host's name."""
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
    return os.path.join(backup_root, TRASH_DIRNAME, f"{stamp}-{host_name()}")


def _prune(directory: str, stop: str) -> None:
    """Remove empty directories from ``directory`` up to, not including, stop."""
    stop = os.path.abspath(stop)
    current = os.path.abspath(directory)
    while current != stop and current.startswith(stop + os.sep):
        try:
            os.rmdir(current)
        except OSError:
            return
        current = os.path.dirname(current)


def describe(deletion: Deletion) -> str:
    """Why a deletion happens, for the user."""
    if deletion.here:
        return f"{deletion.key} (deleted on this machine)"
    return f"{deletion.key} (deleted on {deletion.host or 'another machine'})"


def execute(
    plan: list[Deletion],
    backup_root: str,
    dry_run: bool = False,
    verbose: bool = False,
    now: float | None = None,
) -> dict[str, int]:
    """Move every victim to the trash; tombstone what was deleted here."""
    stats = {"deleted": 0, "errors": 0}
    if not plan:
        return stats
    now = time.time() if now is None else now
    run_dir = trash_run_dir(backup_root, now)
    tombstoned: list[str] = []
    for deletion in plan:
        if verbose:
            print(utils.colorize_message(f"Deleting {describe(deletion)}"))
        failed = False
        for victim in deletion.victims:
            if verbose:
                print(utils.colorize_message(f"  {victim.path}"))
            if dry_run:
                stats["deleted"] += 1
                continue
            target = os.path.join(run_dir, victim.trash_rel)
            try:
                os.makedirs(os.path.dirname(target), exist_ok=True)
                shutil.move(victim.path, target)
            except OSError as e:
                print(
                    utils.colorize_message(
                        f"Error: Unable to move {victim.path} to the trash: {e}",
                    ),
                )
                stats["errors"] += 1
                failed = True
                continue
            _prune(os.path.dirname(victim.path), victim.stop)
            stats["deleted"] += 1
        if deletion.here and not failed:
            tombstoned.append(deletion.key)
    if tombstoned and not dry_run:
        record_tombstones(backup_root, tombstoned, now)
    return stats


def refresh_snapshot(  # noqa: PLR0913
    snapshot: dict[str, list[int]],
    backup_root: str,
    home: str,
    source: str,
    dests: list[str],
    *,
    globs: ignore.Globs = (),
) -> None:
    """Replace one group's snapshot entries with what is on disk now.

    An unavailable destination keeps its old entries, so a file deleted after
    its parent directory comes back is still recognized as a deletion.
    """
    for member in group_members(backup_root, home, source, dests):
        if not _available(member):
            continue
        prefix = _snapshot_key(member.side, member.rel)
        below = prefix + os.sep
        for key in [k for k in snapshot if k == prefix or k.startswith(below)]:
            del snapshot[key]
        for rel in member_files(member.path, globs):
            current = fingerprint(_join(member.path, rel))
            if isinstance(current, list):
                snapshot[_snapshot_key(member.side, _join(member.rel, rel))] = current


# --- migration ----------------------------------------------------------------


def legacy_tombstones(
    backup_root: str,
    groups: dict[str, list[str]],
    normalize: Callable[[str], str],
) -> dict[str, dict]:
    """Read the old ``mackup rm`` log as tombstones, without touching it.

    Each line names a destination or a path below one; it is mapped to the
    backup path it stands for, and lines that match nothing are dropped. The
    log carries no timestamps, so every entry is dated by the log's own mtime
    — the time of the last removal. The log is left in place: machines still
    running an older mackup-ng depend on it, so delete it by hand once every
    machine is upgraded.
    """
    legacy = os.path.join(backup_root, LEGACY_DELETIONS_FILENAME)
    try:
        deleted_at = os.path.getmtime(legacy)
        with open(legacy, encoding="utf-8") as handle:
            lines = [line.strip() for line in handle if line.strip()]
    except OSError:
        return {}

    found: dict[str, dict] = {}
    for line in lines:
        dest = normalize(line)
        for source, group_dests in groups.items():
            for group_dest in group_dests:
                root = os.path.normpath(group_dest)
                if dest == root:
                    key = os.path.normpath(source)
                elif dest.startswith(root + os.sep):
                    key = os.path.join(source, dest[len(root) + 1 :])
                else:
                    continue
                found[key] = {"ts": deleted_at, "host": LEGACY_HOST}
    return found
