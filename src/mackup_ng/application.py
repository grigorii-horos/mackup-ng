"""
Application Profile.

An Application Profile contains all the information about an application in
Mackup. Name, files, ...
"""

import os
from typing import Union

from . import utils
from .mackup import Mackup

DELETIONS_FILENAME = ".mackup-deletions"


class ApplicationProfile:
    """Instantiate this class with application specific data."""

    def __init__(
        self,
        mackup: Mackup,
        files: Union[set[str], set[tuple[str, str]]],
        dry_run: bool,
        verbose: bool,
    ) -> None:
        """
        Create an ApplicationProfile instance.

        Args:
            mackup (Mackup)
            files (list)
        """
        assert isinstance(mackup, Mackup)
        assert isinstance(files, set)

        self.mackup: Mackup = mackup
        self.file_entries: list[tuple[str, str]]
        if all(isinstance(item, str) for item in files):
            raw_files = files
            assert all(isinstance(item, str) for item in raw_files)
            self.files = sorted(raw_files)
            self.file_entries = [(path, path) for path in self.files]
        else:
            raw_mappings = files
            assert all(isinstance(item, tuple) and len(item) == 2 for item in raw_mappings)
            mappings = {(str(local), str(backup)) for (local, backup) in raw_mappings}
            self.file_entries = sorted(mappings)
            self.files = [local for (local, _backup) in self.file_entries]
        self.dry_run: bool = dry_run
        self.verbose: bool = verbose

    @staticmethod
    def _print(message: str) -> None:
        """Print a user-facing message with terminal color highlighting."""
        print(utils.colorize_message(message))

    def get_filepaths(self, local_filename: str, backup_filename: str | None = None) -> tuple[str, str]:
        """
        Get home and mackup filepaths for given file

        Args:
            local_filename (str)
            backup_filename (str|None)

        Returns:
            home_filepath, mackup_filepath (str, str)
        """
        return (
            os.path.join(os.environ["HOME"], local_filename),
            os.path.join(self.mackup.mackup_folder, backup_filename or local_filename),
        )

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
            for path in sorted(deleted_files):
                f.write(f"{path}\n")

    def record_deleted_file(self, local_filename: str) -> None:
        """Persist an explicit deletion tombstone for a managed path."""
        deleted_files = self.read_deleted_files()
        deleted_files.add(self.normalize_relative_path(local_filename))
        self.write_deleted_files(deleted_files)

    def apply_deleted_files(self) -> dict[str, int]:
        """Apply deletion tombstones for this app before normal sync."""
        stats: dict[str, int] = {"deleted": 0, "errors": 0}
        deleted_files = self.read_deleted_files()
        if not deleted_files:
            return stats

        for local_filename, backup_filename in self.file_entries:
            if self.normalize_relative_path(local_filename) not in deleted_files:
                continue

            home_filepath, mackup_filepath = self.get_filepaths(
                local_filename, backup_filename,
            )
            deleted_any = False
            for filepath in (home_filepath, mackup_filepath):
                if not os.path.lexists(filepath):
                    continue
                if self.verbose:
                    self._print(f"Deleting\n  {filepath} ...")
                if self.dry_run:
                    deleted_any = True
                    continue
                try:
                    utils.delete(filepath)
                    deleted_any = True
                except PermissionError as e:
                    self._print(
                        f"Error: Unable to delete file {filepath} "
                        f"due to permission issue: {e}",
                    )
                    stats["errors"] += 1
            if deleted_any:
                stats["deleted"] += 1

        return stats

    def remove_file(self, local_filename: str, backup_filename: str) -> dict[str, int]:
        """Explicitly remove one managed file locally and from backup storage."""
        stats: dict[str, int] = {"deleted": 0, "errors": 0}
        home_filepath, mackup_filepath = self.get_filepaths(
            local_filename, backup_filename,
        )

        if self.verbose:
            self._print(
                f"Deleting\n  {home_filepath}\n  and\n  {mackup_filepath} ...",
            )

        if self.dry_run:
            stats["deleted"] += 1
            return stats

        for filepath in (home_filepath, mackup_filepath):
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
            self.record_deleted_file(local_filename)
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
                    if entry_mtime > latest_mtime:
                        latest_mtime = entry_mtime

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

    def sync_directory_entries_one_way(
        self, source_dir: str, destination_dir: str, source_wins: bool, dry_run: bool,
    ) -> bool:
        """
        Sync directory entries from source to destination by per-entry mtime.

        When source_wins is True, newer source entries overwrite destination.
        When source_wins is False, this still compares mtimes but never copies
        destination back to source; useful for skip-only behavior.
        """
        changed = False
        source_root_mtime = os.path.getmtime(source_dir)
        destination_root_mtime = os.path.getmtime(destination_dir)
        if source_wins and source_root_mtime > destination_root_mtime:
            if not dry_run:
                os.utime(destination_dir, (source_root_mtime, source_root_mtime))
            changed = True

        source_entries = self.collect_relative_entries(source_dir)
        destination_entries = self.collect_relative_entries(destination_dir)
        all_entries = sorted(source_entries | destination_entries)

        for entry in all_entries:
            source_entry = os.path.join(source_dir, entry)
            destination_entry = os.path.join(destination_dir, entry)
            source_exists = os.path.exists(source_entry)
            destination_exists = os.path.exists(destination_entry)

            if source_exists and destination_exists:
                source_is_dir = os.path.isdir(source_entry)
                destination_is_dir = os.path.isdir(destination_entry)

                if source_is_dir and destination_is_dir:
                    source_mtime = os.path.getmtime(source_entry)
                    destination_mtime = os.path.getmtime(destination_entry)
                    if source_wins and source_mtime > destination_mtime:
                        if not dry_run:
                            os.utime(destination_entry, (source_mtime, source_mtime))
                        changed = True
                    continue

                source_mtime = self.get_effective_mtime(source_entry)
                destination_mtime = self.get_effective_mtime(destination_entry)

                if source_wins and source_mtime > destination_mtime:
                    if not dry_run:
                        if source_is_dir:
                            if os.path.lexists(destination_entry) and not destination_is_dir:
                                utils.delete(destination_entry)
                            self.ensure_directory(destination_entry, source_entry)
                        else:
                            self.copy_item(source_entry, destination_entry)
                    changed = True
            elif source_exists:
                if not dry_run:
                    if os.path.isdir(source_entry):
                        self.ensure_directory(destination_entry, source_entry)
                    else:
                        self.copy_item(source_entry, destination_entry)
                changed = True

        return changed

    def sync_directory_entries(self, home_dir: str, backup_dir: str) -> bool:
        """
        Synchronize two directories by comparing mtime per entry.

        Returns True if any files were actually copied or updated.
        """
        changed = False

        home_root_mtime = os.path.getmtime(home_dir)
        backup_root_mtime = os.path.getmtime(backup_dir)
        if home_root_mtime > backup_root_mtime:
            os.utime(backup_dir, (home_root_mtime, home_root_mtime))
        elif backup_root_mtime > home_root_mtime:
            os.utime(home_dir, (backup_root_mtime, backup_root_mtime))

        home_entries = self.collect_relative_entries(home_dir)
        backup_entries = self.collect_relative_entries(backup_dir)
        all_entries = sorted(home_entries | backup_entries)

        for entry in all_entries:
            home_entry = os.path.join(home_dir, entry)
            backup_entry = os.path.join(backup_dir, entry)
            home_exists = os.path.exists(home_entry)
            backup_exists = os.path.exists(backup_entry)

            if home_exists and backup_exists:
                home_is_dir = os.path.isdir(home_entry)
                backup_is_dir = os.path.isdir(backup_entry)

                if home_is_dir and backup_is_dir:
                    home_mtime = os.path.getmtime(home_entry)
                    backup_mtime = os.path.getmtime(backup_entry)
                    if home_mtime > backup_mtime:
                        os.utime(backup_entry, (home_mtime, home_mtime))
                    elif backup_mtime > home_mtime:
                        os.utime(home_entry, (backup_mtime, backup_mtime))
                    continue

                if (not home_is_dir) and (not backup_is_dir):
                    home_mtime = os.path.getmtime(home_entry)
                    backup_mtime = os.path.getmtime(backup_entry)
                    if home_mtime > backup_mtime:
                        if self.verbose:
                            self._print(f"Backing up {entry}")
                        self.copy_item(home_entry, backup_entry)
                        changed = True
                    elif backup_mtime > home_mtime:
                        if self.verbose:
                            self._print(f"Restoring {entry}")
                        self.copy_item(backup_entry, home_entry)
                        changed = True
                    continue

                home_mtime = self.get_effective_mtime(home_entry)
                backup_mtime = self.get_effective_mtime(backup_entry)
                if home_mtime >= backup_mtime:
                    if home_is_dir:
                        if os.path.lexists(backup_entry) and not backup_is_dir:
                            utils.delete(backup_entry)
                        self.ensure_directory(backup_entry, home_entry)
                    else:
                        if self.verbose:
                            self._print(f"Backing up {entry}")
                        self.copy_item(home_entry, backup_entry)
                    changed = True
                else:
                    if backup_is_dir:
                        if os.path.lexists(home_entry) and not home_is_dir:
                            utils.delete(home_entry)
                        self.ensure_directory(home_entry, backup_entry)
                    else:
                        if self.verbose:
                            self._print(f"Restoring {entry}")
                        self.copy_item(backup_entry, home_entry)
                    changed = True
            elif home_exists:
                if self.verbose:
                    self._print(f"Backing up {entry}")
                if os.path.isdir(home_entry):
                    self.ensure_directory(backup_entry, home_entry)
                else:
                    self.copy_item(home_entry, backup_entry)
                changed = True
            elif backup_exists:
                if self.verbose:
                    self._print(f"Restoring {entry}")
                if os.path.isdir(backup_entry):
                    self.ensure_directory(home_entry, backup_entry)
                else:
                    self.copy_item(backup_entry, home_entry)
                changed = True

        return changed

    def sync_files(self) -> dict[str, int]:
        """Synchronize files between home and Mackup using mtime."""
        stats: dict[str, int] = {
            "backed_up": 0, "restored": 0, "synchronized": 0,
            "deleted": 0, "skipped": 0, "errors": 0,
        }
        deletion_stats = self.apply_deleted_files()
        stats["deleted"] += deletion_stats["deleted"]
        stats["errors"] += deletion_stats["errors"]
        deleted_files = self.read_deleted_files()

        for local_filename, backup_filename in self.file_entries:
            if self.normalize_relative_path(local_filename) in deleted_files:
                continue

            (home_filepath, mackup_filepath) = self.get_filepaths(local_filename, backup_filename)

            home_exists = os.path.isfile(home_filepath) or os.path.isdir(home_filepath)
            backup_exists = os.path.isfile(mackup_filepath) or os.path.isdir(
                mackup_filepath
            )

            action: str | None = None
            if home_exists and backup_exists:
                # Already linked/same inode, nothing to do.
                if os.path.samefile(home_filepath, mackup_filepath):
                    if self.verbose:
                        self._print(
                            f"Skipping {home_filepath}\n"
                            f"  already linked to\n  {mackup_filepath}",
                        )
                    stats["skipped"] += 1
                    continue

                # For directories we merge by entry mtime, not whole-tree mtime.
                if os.path.isdir(home_filepath) and os.path.isdir(mackup_filepath):
                    if self.dry_run:
                        home_to_backup_changes = self.sync_directory_entries_one_way(
                            home_filepath, mackup_filepath, source_wins=True, dry_run=True,
                        )
                        backup_to_home_changes = self.sync_directory_entries_one_way(
                            mackup_filepath, home_filepath, source_wins=True, dry_run=True,
                        )
                        dir_changed = home_to_backup_changes or backup_to_home_changes
                        if self.verbose:
                            if dir_changed:
                                self._print(
                                    f"Synchronizing\n  {home_filepath}\n  with\n  {mackup_filepath} ...",
                                )
                            else:
                                self._print(
                                    f"Skipping {home_filepath}\n"
                                    f"  already in sync with\n  {mackup_filepath}",
                                )
                        if dir_changed:
                            stats["synchronized"] += 1
                        else:
                            stats["skipped"] += 1
                        continue

                    try:
                        dir_changed = self.sync_directory_entries(home_filepath, mackup_filepath)
                        if dir_changed:
                            if self.verbose:
                                self._print(
                                    f"Synchronizing\n  {home_filepath}\n  with\n  {mackup_filepath} ...",
                                )
                            stats["synchronized"] += 1
                        else:
                            if self.verbose:
                                self._print(
                                    f"Skipping {home_filepath}\n"
                                    f"  already in sync with\n  {mackup_filepath}",
                                )
                            stats["skipped"] += 1
                    except PermissionError as e:
                        self._print(
                            "Error: Unable to sync directory entries between "
                            f"{home_filepath} and {mackup_filepath} due to permission issue: {e}",
                        )
                        stats["errors"] += 1
                    continue

                home_mtime = self.get_effective_mtime(home_filepath)
                backup_mtime = self.get_effective_mtime(mackup_filepath)
                if home_mtime > backup_mtime:
                    action = "backup"
                elif backup_mtime > home_mtime:
                    action = "restore"
            elif home_exists:
                action = "backup"
            elif backup_exists:
                action = "restore"
            else:
                # Missing on both sides: no-op, do not count as a user-visible skip.
                continue

            if action is None:
                if self.verbose:
                    self._print(
                        f"Skipping {home_filepath}\n"
                        f"  same mtime as\n  {mackup_filepath}",
                    )
                stats["skipped"] += 1
                continue

            if action == "backup":
                if self.verbose:
                    self._print(
                        f"Backing up\n  {home_filepath}\n  to\n  {mackup_filepath} ...",
                    )

                if self.dry_run:
                    stats["backed_up"] += 1
                    continue

                if os.path.lexists(mackup_filepath):
                    utils.delete(mackup_filepath)

                try:
                    utils.copy(home_filepath, mackup_filepath)
                    stats["backed_up"] += 1
                except PermissionError as e:
                    self._print(
                        f"Error: Unable to copy file from {home_filepath} to "
                        f"{mackup_filepath} due to permission issue: {e}",
                    )
                    stats["errors"] += 1
            else:
                if self.verbose:
                    self._print(
                        f"Restoring\n  {mackup_filepath}\n  to\n  {home_filepath} ...",
                    )

                if self.dry_run:
                    stats["restored"] += 1
                    continue

                if os.path.lexists(home_filepath):
                    utils.delete(home_filepath)

                try:
                    utils.copy(mackup_filepath, home_filepath)
                    stats["restored"] += 1
                except PermissionError as e:
                    self._print(
                        f"Error: Unable to copy file from {mackup_filepath} to "
                        f"{home_filepath} due to permission issue: {e}",
                    )
                    stats["errors"] += 1

        return stats
