"""
Application Profile.

An Application Profile contains all the information about an application in
Mackup. Name, files, ...
"""

import math
import os
from collections.abc import Callable

from . import ignore, utils
from .mackup import Mackup

DELETIONS_FILENAME = ".mackup-deletions"


class ApplicationProfile:
    """Instantiate this class with application specific data."""

    def __init__(
        self,
        mackup: Mackup,
        dry_run: bool,
        verbose: bool,
        ignore_globs: ignore.Globs = (),
        prefer_backup: bool = False,
    ) -> None:
        """Create an ApplicationProfile bound to a Mackup storage folder.

        The sync engine works on the groups handed to :meth:`sync_group`, so
        the profile itself carries no file list. ``ignore_globs`` are the names
        this profile must leave alone in both directions: the global ignore
        files plus whatever the config being synced adds.

        ``prefer_backup`` gives the backup side authority over every contest,
        whatever the timestamps say. ``mackup init`` needs it: on a fresh
        machine the applications you just installed have written their default
        configs with today's timestamp, so the ordinary newest-wins rule would
        let those defaults beat the real settings in the backup — and then
        propagate them to every other machine on the next sync.
        """
        assert isinstance(mackup, Mackup)
        self.mackup: Mackup = mackup
        self.dry_run: bool = dry_run
        self.verbose: bool = verbose
        self.ignore_globs: ignore.Globs = tuple(ignore_globs)
        self.prefer_backup: bool = prefer_backup
        # Trailing separator so a sibling folder whose name merely starts with
        # the backup folder's name is not mistaken for a path inside it.
        self.backup_root: str = os.path.join(mackup.mackup_folder, "")

    def effective_mtime(self, path: str) -> float:
        """This profile's :meth:`get_effective_mtime`, ignores applied.

        Under ``prefer_backup`` a path inside the backup folder reports an
        infinite mtime. Every contest in this engine — newest member wins, and
        the "not older than the winner" skip that follows it — resolves through
        this one comparison, so making the backup unbeatable here makes it
        unbeatable everywhere, with no second rule to keep in step. A path the
        backup does not hold is simply not a candidate, so the local side still
        reaches the backup on its own.
        """
        if self.prefer_backup and path.startswith(self.backup_root):
            return math.inf
        return self.get_effective_mtime(path, self.ignore_globs)

    def copytree_ignore(self) -> Callable[[str, list[str]], set[str]]:
        """This profile's ignore callable for whole-folder copies."""
        return ignore.copytree_ignore(self.ignore_globs)

    @staticmethod
    def _print(message: str) -> None:
        """Print a user-facing message with terminal color highlighting."""
        print(utils.colorize_message(message))

    def member_paths(self, source: str, dests: list[str]) -> list[str]:
        """Absolute paths of a fanout group: the backup source, then dests."""
        return [
            os.path.join(self.mackup.mackup_folder, source),
            *(os.path.join(os.environ["HOME"], dest) for dest in dests),
        ]

    @staticmethod
    def new_stats() -> dict[str, int]:
        """A zeroed statistics dict with every key the reporter expects."""
        return {
            "backed_up": 0,
            "restored": 0,
            "synchronized": 0,
            "deleted": 0,
            "skipped": 0,
            "errors": 0,
        }

    def sync_group(self, source: str, dests: list[str]) -> dict[str, int]:
        """Sync one fanout group: newest member wins and reaches every other.

        Members are peers — the backup side has no special authority. A group
        of two members is the ordinary 1:1 case.
        """
        stats = self.new_stats()
        members = self.member_paths(source, dests)
        backup_path = members[0]
        existing = self.existing_members(members)
        if not existing:
            return stats

        if len({os.path.isdir(path) for path in existing}) > 1:
            # Members disagree on file vs. directory. Resolve the clash first:
            # the newest member replaces every member of the other type, the
            # way the pairwise engine did. Otherwise a directory merge would
            # try to makedirs() over a regular file and blow up the whole run.
            for key, value in self.replace_clashing_members(
                existing,
                backup_path,
            ).items():
                stats[key] += value
            existing = self.existing_members(members)
            if not existing or len({os.path.isdir(p) for p in existing}) > 1:
                # Dry run (nothing was written) or a failed replacement.
                return stats

        if any(os.path.isdir(path) for path in existing):
            for key, value in self.sync_members_directory(members).items():
                stats[key] += value
            return stats

        for key, value in self.sync_members_file(members, backup_path).items():
            stats[key] += value
        return stats

    @staticmethod
    def existing_members(members: list[str]) -> list[str]:
        """The members that currently exist as a regular file or a directory."""
        return [path for path in members if os.path.isfile(path) or os.path.isdir(path)]

    def replace_clashing_members(
        self,
        existing: list[str],
        backup_path: str,
    ) -> dict[str, int]:
        """Replace members whose type differs from the newest member's.

        The newest member wins wholesale: a loser of the other type is deleted
        and replaced by a copy of the winner. Members of the winner's type are
        left to the normal entry-by-entry sync.
        """
        stats = self.new_stats()
        winner = max(existing, key=self.effective_mtime)
        winner_is_dir = os.path.isdir(winner)

        for member in existing:
            if member == winner or os.path.isdir(member) == winner_is_dir:
                continue
            if self.verbose:
                self._print(
                    f"Replacing\n  {member}\n  with\n  {winner}\n"
                    "  (file/directory type conflict)",
                )
            if not self.dry_run:
                try:
                    utils.delete(member)
                    utils.copy(winner, member, self.copytree_ignore())
                except OSError as e:
                    self._print(
                        f"Error: Unable to replace {member} with {winner}: {e}",
                    )
                    stats["errors"] += 1
                    continue
            if member == backup_path:
                stats["backed_up"] += 1
            else:
                stats["restored"] += 1
        return stats

    def sync_members_file(
        self,
        members: list[str],
        backup_path: str,
    ) -> dict[str, int]:
        """Sync a group whose members are all regular files."""
        stats = self.new_stats()
        existing = self.existing_members(members)
        if not existing:
            return stats

        winner = max(existing, key=self.effective_mtime)
        winner_mtime = self.effective_mtime(winner)

        for member in members:
            if member == winner:
                continue
            if os.path.exists(member):
                if os.path.samefile(member, winner):
                    if self.verbose:
                        self._print(
                            f"Skipping {member}\n  already linked to\n  {winner}",
                        )
                    stats["skipped"] += 1
                    continue
                if self.effective_mtime(member) >= winner_mtime:
                    if self.verbose:
                        self._print(
                            f"Skipping {member}\n  not older than\n  {winner}",
                        )
                    stats["skipped"] += 1
                    continue

            if self.verbose:
                self._print(f"Copying\n  {winner}\n  to\n  {member} ...")

            if not self.dry_run:
                try:
                    if os.path.lexists(member):
                        utils.delete(member)
                    utils.copy(winner, member, self.copytree_ignore())
                except OSError as e:
                    # Any OSError disqualifies just this member: no permission,
                    # but also a plain file where a parent directory belongs.
                    self._print(
                        f"Error: Unable to copy file from {winner} to {member}: {e}",
                    )
                    stats["errors"] += 1
                    continue

            if member == backup_path:
                stats["backed_up"] += 1
            else:
                stats["restored"] += 1

        return stats

    def sync_members_directory(self, members: list[str]) -> dict[str, int]:
        """Merge N directory members entry by entry; newest entry wins.

        Every member ends up holding the union of the group's entries. A
        member that does not exist yet is created, so a backup directory can
        fan out to fresh destinations.
        """
        stats = self.new_stats()
        present = [path for path in members if os.path.isdir(path)]
        if not present:
            return stats

        root_source = max(present, key=os.path.getmtime)
        failed_members: set[str] = set()
        if not self.dry_run:
            for member in members:
                try:
                    self.ensure_directory(member, root_source)
                except OSError as e:
                    # Any OSError (no permission, but also a plain file where
                    # the directory should go) disqualifies just this member.
                    self._print(
                        f"Error: Unable to create directory {member}: {e}",
                    )
                    stats["errors"] += 1
                    failed_members.add(member)

        entries: list[str] = []
        for member in present:
            for entry in sorted(
                self.collect_relative_entries(member, self.ignore_globs),
            ):
                if entry not in entries:
                    entries.append(entry)

        changed = False
        for entry in entries:
            targets = [os.path.join(member, entry) for member in members]
            holders = [path for path in targets if os.path.exists(path)]
            if not holders:
                continue
            winner = max(holders, key=self.effective_mtime)
            winner_mtime = self.effective_mtime(winner)
            winner_is_dir = os.path.isdir(winner)

            for target, member in zip(targets, members, strict=True):
                if target == winner:
                    continue
                if member in failed_members:
                    continue
                if (
                    os.path.exists(target)
                    and os.path.isdir(target) == winner_is_dir
                    and self.effective_mtime(target) >= winner_mtime
                ):
                    continue
                if self.dry_run:
                    changed = True
                    continue
                try:
                    if winner_is_dir:
                        if os.path.lexists(target) and not os.path.isdir(target):
                            utils.delete(target)
                        self.ensure_directory(target, winner)
                    else:
                        if self.verbose:
                            self._print(f"Copying {entry} to {target}")
                        self.copy_item(winner, target)
                except OSError as e:
                    self._print(
                        f"Error: Unable to copy {winner} to {target}: {e}",
                    )
                    stats["errors"] += 1
                    continue
                changed = True

        if changed:
            stats["synchronized"] += 1
        else:
            stats["skipped"] += 1
        return stats

    def get_deletions_filepath(self) -> str:
        """Return the backup-side file that records explicit removals."""
        return os.path.join(self.mackup.mackup_folder, DELETIONS_FILENAME)

    @staticmethod
    def normalize_relative_path(path: str) -> str:
        """Normalize a user/log path to a relative Mackup config path."""
        normalized = os.path.normpath(os.path.expanduser(path))
        home = os.path.abspath(os.environ["HOME"])
        if os.path.isabs(normalized):
            normalized_abs = os.path.abspath(normalized)
            try:
                normalized = os.path.relpath(normalized_abs, home)
            except ValueError:
                normalized = normalized_abs
        while normalized.startswith(f".{os.sep}"):
            normalized = normalized[2:]
        return normalized

    def read_deleted_files(self) -> set[str]:
        """Read explicit deletion tombstones from backup storage."""
        deletions_filepath = self.get_deletions_filepath()
        if not os.path.exists(deletions_filepath):
            return set()

        deleted_files: set[str] = set()
        with open(deletions_filepath, encoding="utf-8") as f:
            for line in f:
                path = line.strip()
                if path:
                    deleted_files.add(self.normalize_relative_path(path))
        return deleted_files

    def write_deleted_files(self, deleted_files: set[str]) -> None:
        """Write explicit deletion tombstones to backup storage."""
        deletions_filepath = self.get_deletions_filepath()
        os.makedirs(os.path.dirname(deletions_filepath), exist_ok=True)
        with open(deletions_filepath, "w", encoding="utf-8") as f:
            f.writelines(f"{path}\n" for path in sorted(deleted_files))

    def record_deleted_file(self, local_filename: str) -> None:
        """Persist an explicit deletion tombstone for a managed path."""
        deleted_files = self.read_deleted_files()
        deleted_files.add(self.normalize_relative_path(local_filename))
        self.write_deleted_files(deleted_files)

    def read_tombstones(self) -> set[str]:
        """Normalized destinations recorded as explicitly removed."""
        return self.read_deleted_files()

    @staticmethod
    def relative_tombstone(tombstone: str, root: str) -> str | None:
        """The part of ``tombstone`` below ``root``, or None if not below it."""
        prefix = root + os.sep
        if tombstone.startswith(prefix) and len(tombstone) > len(prefix):
            return tombstone[len(prefix) :]
        return None

    def group_tombstoned_relatives(
        self,
        live_dests: list[str],
        tombstoned: set[str],
    ) -> list[str]:
        """Relative paths tombstoned *inside* one of the group's destinations.

        `mackup rm ~/.work.d/foo` tombstones `.work.d/foo`, a path below a
        managed destination rather than a destination itself. Group members
        mirror each other, so such a removal applies to the whole group.
        """
        relatives: list[str] = []
        ordered = sorted(tombstoned)
        for dest in live_dests:
            root = self.normalize_relative_path(dest)
            for tombstone in ordered:
                relative = self.relative_tombstone(tombstone, root)
                if relative is not None and relative not in relatives:
                    relatives.append(relative)
        return relatives

    def apply_tombstones(
        self,
        groups: dict[str, list[str]],
        tombstoned: set[str],
    ) -> dict[str, int]:
        """Delete tombstoned destinations, and sources left with no destination.

        ``groups`` is the unfiltered plan: it still contains the tombstoned
        destinations, so a source can tell whether any live destination
        remains before it is deleted.

        A tombstone recorded *below* a destination (a single file inside a
        managed directory) is enforced across every member of the group — the
        source and each sibling destination — so the directory merge that runs
        afterwards finds no copy left to resurrect.
        """
        stats = self.new_stats()
        if not tombstoned:
            return stats
        for source, dests in groups.items():
            dead = [
                dest
                for dest in dests
                if self.normalize_relative_path(dest) in tombstoned
            ]
            live = [dest for dest in dests if dest not in dead]
            victims = [os.path.join(os.environ["HOME"], dest) for dest in dead]
            if dead and not live:
                victims.append(os.path.join(self.mackup.mackup_folder, source))
            for relative in self.group_tombstoned_relatives(live, tombstoned):
                victims.extend(
                    os.path.join(os.environ["HOME"], dest, relative) for dest in live
                )
                victims.append(
                    os.path.join(self.mackup.mackup_folder, source, relative),
                )
            for filepath in victims:
                if not os.path.lexists(filepath):
                    continue
                if self.verbose:
                    self._print(f"Deleting\n  {filepath} ...")
                if self.dry_run:
                    stats["deleted"] += 1
                    continue
                try:
                    utils.delete(filepath)
                    stats["deleted"] += 1
                except OSError as e:
                    self._print(
                        f"Error: Unable to delete file {filepath}: {e}",
                    )
                    stats["errors"] += 1
        return stats

    def remove_paths(
        self,
        targets: list[str],
        tombstone_dest: str,
    ) -> dict[str, int]:
        """Delete every path in ``targets`` and record one tombstone."""
        stats = self.new_stats()
        if self.verbose:
            for filepath in targets:
                self._print(f"Deleting\n  {filepath} ...")

        if self.dry_run:
            stats["deleted"] += 1
            return stats

        for filepath in targets:
            if not os.path.lexists(filepath):
                continue
            try:
                utils.delete(filepath)
            except OSError as e:
                self._print(
                    f"Error: Unable to delete file {filepath}: {e}",
                )
                stats["errors"] += 1

        if stats["errors"] == 0:
            self.record_deleted_file(tombstone_dest)
            stats["deleted"] += 1
        return stats

    def remove_destination(
        self,
        source: str,
        dest: str,
        siblings: int,
    ) -> dict[str, int]:
        """Remove one destination; drop the source only when nothing else uses it.

        ``siblings`` is the number of other live destinations fed by ``source``.
        """
        targets = [os.path.join(os.environ["HOME"], dest)]
        if siblings == 0:
            targets.append(os.path.join(self.mackup.mackup_folder, source))
        return self.remove_paths(targets, dest)

    def remove_group_descendant(
        self,
        source_root: str,
        dest_roots: list[str],
        relative_path: str,
        tombstone_dest: str,
    ) -> dict[str, int]:
        """Remove one path inside a managed directory, from every group member.

        The members of a fanout group mirror each other, so a file removed
        from one of them must go from the source and the siblings too —
        otherwise the next directory merge copies it straight back.
        """
        targets = [
            os.path.join(os.environ["HOME"], dest_root, relative_path)
            for dest_root in dest_roots
        ]
        targets.append(
            os.path.join(self.mackup.mackup_folder, source_root, relative_path),
        )
        return self.remove_paths(targets, tombstone_dest)

    @staticmethod
    def get_effective_mtime(path: str, globs: ignore.Globs = ()) -> float:
        """
        Return comparable mtime for a file or directory.

        For directories, the newest mtime in the whole tree is used so changes
        to nested files/folders are considered during sync. Ignored entries are
        left out: a conflict copy Syncthing wrote a second ago would otherwise
        make its whole side look newer than a real edit on the other machine.
        """
        latest_mtime = os.path.getmtime(path)

        if os.path.isdir(path):
            for root, dirs, files in os.walk(path):
                dirs[:] = [d for d in dirs if not ignore.is_ignored(globs, d)]
                for name in dirs + files:
                    if ignore.is_ignored(globs, name):
                        continue
                    entry_mtime = os.path.getmtime(os.path.join(root, name))
                    latest_mtime = max(latest_mtime, entry_mtime)

        return latest_mtime

    @staticmethod
    def collect_relative_entries(root: str, globs: ignore.Globs = ()) -> set[str]:
        """Collect all file and directory entries under root (relative paths).

        Ignored names are left out, and ignored directories are not descended
        into, so nothing below them can be carried anywhere either.
        """
        entries: set[str] = set()
        for cur_root, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if not ignore.is_ignored(globs, d)]
            for name in dirs + files:
                if ignore.is_ignored(globs, name):
                    continue
                entries.add(os.path.relpath(os.path.join(cur_root, name), root))
        return entries

    def copy_item(self, source: str, destination: str) -> None:
        """
        Copy source item to destination, replacing destination when types differ.
        """
        if os.path.lexists(destination):
            source_is_dir = os.path.isdir(source)
            destination_is_dir = os.path.isdir(destination)
            if source_is_dir != destination_is_dir:
                utils.delete(destination)
        utils.copy(source, destination, self.copytree_ignore())

    @staticmethod
    def ensure_directory(path: str, mode_from: str) -> None:
        """Ensure directory exists and mirror mtime from mode_from."""
        os.makedirs(path, exist_ok=True)
        dir_mtime = os.path.getmtime(mode_from)
        os.utime(path, (dir_mtime, dir_mtime))
