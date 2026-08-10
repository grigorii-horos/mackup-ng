"""
Application Profile.

An Application Profile contains all the information about an application in
Mackup. Name, files, ...
"""

import os

from . import utils
from .mackup import Mackup

DELETIONS_FILENAME = ".mackup-deletions"


class ApplicationProfile:
    """Instantiate this class with application specific data."""

    def __init__(
        self,
        mackup: Mackup,
        files: set[str] | set[tuple[str, str]] | list[str] | list[tuple[str, str]],
        dry_run: bool,
        verbose: bool,
    ) -> None:
        """Create an ApplicationProfile bound to a Mackup storage folder.

        ``files`` is kept for callers that still pass a declaration list; the
        sync engine works on groups handed to :meth:`sync_group`.
        """
        assert isinstance(mackup, Mackup)
        self.mackup: Mackup = mackup
        self.files = sorted(str(item) for item in files)
        self.dry_run: bool = dry_run
        self.verbose: bool = verbose

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
            "backed_up": 0, "restored": 0, "synchronized": 0,
            "deleted": 0, "skipped": 0, "errors": 0,
        }

    def sync_group(self, source: str, dests: list[str]) -> dict[str, int]:
        """Sync one fanout group: newest member wins and reaches every other.

        Members are peers — the backup side has no special authority. A group
        of two members is the ordinary 1:1 case.
        """
        stats = self.new_stats()
        members = self.member_paths(source, dests)
        backup_path = members[0]
        existing = [
            path for path in members
            if os.path.isfile(path) or os.path.isdir(path)
        ]
        if not existing:
            return stats

        if any(os.path.isdir(path) for path in existing):
            return self.sync_members_directory(members)

        winner = max(existing, key=self.get_effective_mtime)
        winner_mtime = self.get_effective_mtime(winner)

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
                if self.get_effective_mtime(member) >= winner_mtime:
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
                    utils.copy(winner, member)
                except PermissionError as e:
                    self._print(
                        f"Error: Unable to copy file from {winner} to "
                        f"{member} due to permission issue: {e}",
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
                except PermissionError as e:
                    self._print(
                        f"Error: Unable to create directory {member} "
                        f"due to permission issue: {e}",
                    )
                    stats["errors"] += 1
                    failed_members.add(member)

        entries: list[str] = []
        for member in present:
            for entry in sorted(self.collect_relative_entries(member)):
                if entry not in entries:
                    entries.append(entry)

        changed = False
        for entry in entries:
            targets = [os.path.join(member, entry) for member in members]
            holders = [path for path in targets if os.path.exists(path)]
            if not holders:
                continue
            winner = max(holders, key=self.get_effective_mtime)
            winner_mtime = self.get_effective_mtime(winner)
            winner_is_dir = os.path.isdir(winner)

            for target, member in zip(targets, members, strict=True):
                if target == winner:
                    continue
                if member in failed_members:
                    continue
                if (
                    os.path.exists(target)
                    and os.path.isdir(target) == winner_is_dir
                    and self.get_effective_mtime(target) >= winner_mtime
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
                except PermissionError as e:
                    self._print(
                        f"Error: Unable to copy {winner} to {target} "
                        f"due to permission issue: {e}",
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

    def apply_tombstones(
        self, groups: dict[str, list[str]], tombstoned: set[str],
    ) -> dict[str, int]:
        """Delete tombstoned destinations, and sources left with no destination.

        ``groups`` is the unfiltered plan: it still contains the tombstoned
        destinations, so a source can tell whether any live destination
        remains before it is deleted.
        """
        stats = self.new_stats()
        for source, dests in groups.items():
            dead = [dest for dest in dests if
                    self.normalize_relative_path(dest) in tombstoned]
            if not dead:
                continue
            victims = [os.path.join(os.environ["HOME"], dest) for dest in dead]
            if len(dead) == len(dests):
                victims.append(os.path.join(self.mackup.mackup_folder, source))
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
                except PermissionError as e:
                    self._print(
                        f"Error: Unable to delete file {filepath} "
                        f"due to permission issue: {e}",
                    )
                    stats["errors"] += 1
        return stats

    def remove_destination(
        self, source: str, dest: str, siblings: int,
    ) -> dict[str, int]:
        """Remove one destination; drop the source only when nothing else uses it.

        ``siblings`` is the number of other live destinations fed by ``source``.
        """
        stats = self.new_stats()
        home_filepath = os.path.join(os.environ["HOME"], dest)
        targets = [home_filepath]
        if siblings == 0:
            targets.append(os.path.join(self.mackup.mackup_folder, source))

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
            except PermissionError as e:
                self._print(
                    f"Error: Unable to delete file {filepath} "
                    f"due to permission issue: {e}",
                )
                stats["errors"] += 1

        if stats["errors"] == 0:
            self.record_deleted_file(dest)
            stats["deleted"] += 1
        return stats

    @staticmethod
    def get_effective_mtime(path: str) -> float:
        """
        Return comparable mtime for a file or directory.

        For directories, the newest mtime in the whole tree is used so changes
        to nested files/folders are considered during sync.
        """
        latest_mtime = os.path.getmtime(path)

        if os.path.isdir(path):
            for root, dirs, files in os.walk(path):
                for name in dirs + files:
                    entry_mtime = os.path.getmtime(os.path.join(root, name))
                    latest_mtime = max(latest_mtime, entry_mtime)

        return latest_mtime

    @staticmethod
    def collect_relative_entries(root: str) -> set[str]:
        """Collect all file and directory entries under root (relative paths)."""
        entries: set[str] = set()
        for cur_root, dirs, files in os.walk(root):
            for name in dirs + files:
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
        utils.copy(source, destination)

    @staticmethod
    def ensure_directory(path: str, mode_from: str) -> None:
        """Ensure directory exists and mirror mtime from mode_from."""
        os.makedirs(path, exist_ok=True)
        dir_mtime = os.path.getmtime(mode_from)
        os.utime(path, (dir_mtime, dir_mtime))

