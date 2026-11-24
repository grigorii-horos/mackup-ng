"""Tests for deletion tracking: snapshot, tombstones, trash."""

import json
import os
import shutil
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from mackup_ng import deletions
from mackup_ng.application import ApplicationProfile
from mackup_ng.mackup import Mackup

OLD = 1_000_000_000  # an mtime well before any tombstone written in a test


class Machine:
    """One computer: its own $HOME and snapshot, sharing the backup folder."""

    def __init__(self, backup: str) -> None:
        self.home = tempfile.mkdtemp(prefix="mackup_del_home_")
        self.backup = backup
        self.snapshot: dict = {}

    def path(self, rel: str) -> str:
        return os.path.join(self.home, rel)

    def plan(self, source, dests, prefer_backup=False):
        return deletions.plan_group(
            self.backup,
            self.home,
            source,
            dests,
            snapshot=self.snapshot,
            tombstones=deletions.load_tombstones(self.backup),
            prefer_backup=prefer_backup,
        )

    def sync(self, source, dests, prefer_backup=False):
        """What `mackup sync` does to one group: delete, merge, remember."""
        plan = self.plan(source, dests, prefer_backup)
        deletions.execute(plan, self.backup)
        mackup = Mock(spec=Mackup)
        mackup.mackup_folder = self.backup
        with patch.dict(os.environ, {"HOME": self.home}):
            ApplicationProfile(mackup, False, False).sync_group(source, dests)
        deletions.refresh_snapshot(
            self.snapshot, self.backup, self.home, source, dests,
        )
        return plan


def write(path: str, content: str = "x", mtime: float | None = OLD) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as handle:
        handle.write(content)
    if mtime is not None:
        os.utime(path, (mtime, mtime))


class DeletionTestCase(unittest.TestCase):
    def setUp(self):
        self.backup = tempfile.mkdtemp(prefix="mackup_del_backup_")
        self.a = Machine(self.backup)
        self.b = Machine(self.backup)
        self.addCleanup(shutil.rmtree, self.backup, True)
        self.addCleanup(shutil.rmtree, self.a.home, True)
        self.addCleanup(shutil.rmtree, self.b.home, True)
        self.group = (".app", [".app"])

    def trash_files(self) -> list[str]:
        root = os.path.join(self.backup, deletions.TRASH_DIRNAME)
        found = []
        for cur, _dirs, files in os.walk(root):
            found.extend(os.path.relpath(os.path.join(cur, f), root) for f in files)
        return found

    def seed_both(self):
        """`.app/{keep,gone}` on A, synced to the backup and then to B."""
        write(self.a.path(".app/keep"))
        write(self.a.path(".app/gone"))
        self.a.sync(*self.group)
        self.b.sync(*self.group)
        assert os.path.exists(self.b.path(".app/gone"))


class TestLocalDeletion(DeletionTestCase):
    def test_deleted_here_is_deleted_from_backup_and_tombstoned(self):
        self.seed_both()
        os.remove(self.a.path(".app/gone"))

        plan = self.a.sync(*self.group)

        assert [(d.key, d.here) for d in plan] == [(".app/gone", True)]
        assert not os.path.exists(os.path.join(self.backup, ".app/gone"))
        assert os.path.exists(os.path.join(self.backup, ".app/keep"))
        assert not os.path.exists(self.a.path(".app/gone"))
        assert ".app/gone" in deletions.load_tombstones(self.backup)
        trashed = self.trash_files()
        assert len(trashed) == 1
        assert trashed[0].endswith(os.path.join("backup", ".app", "gone"))

    def test_deletion_reaches_the_other_machine(self):
        self.seed_both()
        os.remove(self.a.path(".app/gone"))
        self.a.sync(*self.group)

        plan = self.b.sync(*self.group)

        assert [(d.key, d.here) for d in plan] == [(".app/gone", False)]
        assert not os.path.exists(self.b.path(".app/gone"))
        assert os.path.exists(self.b.path(".app/keep"))
        # B does not re-upload it either.
        assert not os.path.exists(os.path.join(self.backup, ".app/gone"))

    def test_no_snapshot_means_no_local_deletion(self):
        write(os.path.join(self.backup, ".app/file"))
        os.makedirs(self.a.path(".app"))

        plan = self.a.sync(*self.group)

        assert plan == []
        assert os.path.exists(self.a.path(".app/file"))

    def test_an_edit_elsewhere_beats_a_deletion_here(self):
        self.seed_both()
        os.remove(self.a.path(".app/gone"))
        write(os.path.join(self.backup, ".app/gone"), "edited on B", OLD + 50)

        plan = self.a.sync(*self.group)

        assert plan == []
        with open(self.a.path(".app/gone")) as handle:
            assert handle.read() == "edited on B"

    def test_whole_file_destination_deleted_here(self):
        group = (".rc", [".rc"])
        write(self.a.path(".rc"))
        self.a.sync(*group)
        os.remove(self.a.path(".rc"))

        plan = self.a.sync(*group)

        assert [d.key for d in plan] == [".rc"]
        assert not os.path.exists(os.path.join(self.backup, ".rc"))

    def test_whole_directory_deleted_here_prunes_the_backup_directory(self):
        write(self.a.path(".app/sub/one"))
        write(self.a.path(".app/two"))
        self.a.sync(*self.group)
        shutil.rmtree(self.a.path(".app"))

        self.a.sync(*self.group)

        assert not os.path.exists(os.path.join(self.backup, ".app"))
        assert not os.path.exists(self.a.path(".app"))

    def test_deletion_after_the_parent_comes_back_is_still_seen(self):
        group = (".mozilla/user.js", [".mozilla/profile/user.js"])
        write(self.a.path(".mozilla/profile/user.js"))
        self.a.sync(*group)
        shutil.rmtree(self.a.path(".mozilla/profile"))
        # A run that could not restore the destination still refreshes.
        deletions.refresh_snapshot(
            self.a.snapshot, self.backup, self.a.home, *group,
        )

        os.makedirs(self.a.path(".mozilla/profile"))
        plan = self.a.sync(*group)

        assert [d.key for d in plan] == [".mozilla/user.js"]
        assert not os.path.exists(self.a.path(".mozilla/profile/user.js"))

    def test_unavailable_destination_is_not_a_deletion(self):
        group = (".mozilla/user.js", [".mozilla/profile/user.js"])
        write(self.a.path(".mozilla/profile/user.js"))
        self.a.sync(*group)
        shutil.rmtree(self.a.path(".mozilla/profile"))

        plan = self.a.sync(*group)

        assert plan == []
        assert os.path.exists(os.path.join(self.backup, ".mozilla/user.js"))

    def test_fanout_deletion_in_one_member_reaches_the_siblings(self):
        group = (".app", [".work", ".home"])
        write(self.a.path(".work/file"))
        self.a.sync(*group)
        assert os.path.exists(self.a.path(".home/file"))
        os.remove(self.a.path(".work/file"))

        self.a.sync(*group)

        for rel in (".work/file", ".home/file"):
            assert not os.path.exists(self.a.path(rel))
        assert not os.path.exists(os.path.join(self.backup, ".app/file"))


class TestRemoteDeletion(DeletionTestCase):
    def tombstone(self, key: str, ts: float) -> None:
        deletions.record_tombstones(self.backup, [key], now=ts)

    def test_changed_local_copy_survives_and_comes_back(self):
        self.seed_both()
        os.remove(self.a.path(".app/gone"))
        self.a.sync(*self.group)
        write(self.b.path(".app/gone"), "edited on B", OLD + 50)

        plan = self.b.sync(*self.group)

        assert plan == []
        assert os.path.exists(os.path.join(self.backup, ".app/gone"))

    def test_without_snapshot_an_older_copy_is_deleted(self):
        write(self.b.path(".app/gone"), mtime=OLD)
        os.makedirs(os.path.join(self.backup, ".app"))
        self.tombstone(".app/gone", time.time() + 5)

        plan = self.b.sync(*self.group)

        assert [d.key for d in plan] == [".app/gone"]
        assert not os.path.exists(self.b.path(".app/gone"))

    def test_without_snapshot_a_newer_copy_is_kept(self):
        write(self.b.path(".app/new"), mtime=OLD + 200)
        os.makedirs(os.path.join(self.backup, ".app"))
        self.tombstone(".app/new", OLD + 100)

        plan = self.b.sync(*self.group)

        assert plan == []
        assert os.path.exists(os.path.join(self.backup, ".app/new"))

    def test_prefer_backup_deletes_even_a_newer_copy(self):
        write(self.b.path(".app/new"), mtime=OLD + 200)
        os.makedirs(os.path.join(self.backup, ".app"))
        self.tombstone(".app/new", OLD + 100)

        plan = self.b.sync(*self.group, prefer_backup=True)

        assert [d.key for d in plan] == [".app/new"]

    def test_missing_from_backup_without_tombstone_is_restored(self):
        self.seed_both()
        os.remove(os.path.join(self.backup, ".app/gone"))

        plan = self.b.sync(*self.group)

        assert plan == []
        assert os.path.exists(os.path.join(self.backup, ".app/gone"))

    def test_tombstone_is_inert_while_the_backup_holds_the_file(self):
        self.seed_both()
        self.tombstone(".app/gone", time.time())

        assert self.b.plan(*self.group) == []

    def test_directory_tombstone_covers_files_below_it(self):
        write(self.b.path(".app/sub/file"), mtime=OLD)
        os.makedirs(os.path.join(self.backup, ".app"))
        self.tombstone(".app/sub", time.time() + 5)

    def test_copy_restored_with_an_old_mtime_after_the_deletion_is_kept(self):
        """`cp -p` / an archive keeps the mtime but not the inode change time."""
        os.makedirs(os.path.join(self.backup, ".app"))
        self.tombstone(".app/file", time.time() - 5)
        write(self.b.path(".app/file"), mtime=OLD)

        plan = self.b.sync(*self.group)

        assert plan == []
        assert os.path.exists(os.path.join(self.backup, ".app/file"))

        self.b.sync(*self.group)

        assert not os.path.exists(self.b.path(".app/sub"))


class TestTombstoneFiles(DeletionTestCase):
    def test_hosts_write_separate_files_and_newest_wins(self):
        folder = deletions.tombstones_dir(self.backup)
        os.makedirs(folder)
        with open(os.path.join(folder, "other.json"), "w") as handle:
            json.dump({"a": 50.0, "b": 10.0}, handle)
        deletions.record_tombstones(self.backup, ["a", "b"], now=30.0)

        merged = deletions.load_tombstones(self.backup)

        assert merged["a"] == {"ts": 50.0, "host": "other"}
        assert merged["b"]["ts"] == 30.0
        assert merged["b"]["host"] == deletions.host_name()

    def test_expired_entries_are_dropped_on_rewrite(self):
        now = 10 * deletions.TOMBSTONE_TTL
        expired = now - 2 * deletions.TOMBSTONE_TTL
        deletions.record_tombstones(self.backup, ["old"], now=expired)
        deletions.record_tombstones(self.backup, ["new"], now=now)

        assert set(deletions.load_tombstones(self.backup)) == {"new"}


class TestExecute(DeletionTestCase):
    def test_dry_run_touches_nothing(self):
        self.seed_both()
        os.remove(self.a.path(".app/gone"))
        plan = self.a.plan(*self.group)

        stats = deletions.execute(plan, self.backup, dry_run=True)

        assert stats["deleted"] == 1
        assert os.path.exists(os.path.join(self.backup, ".app/gone"))
        assert deletions.load_tombstones(self.backup) == {}
        assert self.trash_files() == []

    def test_ignored_files_are_never_tracked(self):
        write(self.a.path(".app/file"))
        write(self.a.path(".app/x.sync-conflict-1"))
        deletions.refresh_snapshot(
            self.a.snapshot, self.backup, self.a.home, ".app", [".app"],
            globs=("*.sync-conflict-*",),
        )

        assert list(self.a.snapshot) == ["home:" + os.path.join(".app", "file")]


class TestLegacyLog(DeletionTestCase):
    def legacy(self, text: str, mtime: float) -> str:
        path = os.path.join(self.backup, deletions.LEGACY_DELETIONS_FILENAME)
        write(path, text, mtime)
        return path

    def test_legacy_log_is_read_as_tombstones_dated_by_its_mtime(self):
        path = self.legacy(".work.d/foo\n.rc\n.unknown\n", OLD + 10)

        found = deletions.legacy_tombstones(
            self.backup,
            {".d": [".work.d"], ".rc": [".rc"]},
            ApplicationProfile.normalize_relative_path,
        )

        assert set(found) == {os.path.join(".d", "foo"), ".rc"}
        assert {entry["ts"] for entry in found.values()} == {OLD + 10}
        # Left in place for machines still running the old version.
        assert os.path.exists(path)
        assert deletions.load_tombstones(self.backup) == {}

    def test_a_copy_recreated_after_the_legacy_removal_is_kept(self):
        self.legacy(".app/new\n", OLD)
        write(self.b.path(".app/new"), mtime=OLD - 100)  # ctime is now
        os.makedirs(os.path.join(self.backup, ".app"))
        found = deletions.legacy_tombstones(
            self.backup, {".app": [".app"]},
            ApplicationProfile.normalize_relative_path,
        )

        plan = deletions.plan_group(
            self.backup, self.b.home, ".app", [".app"],
            snapshot={}, tombstones=found,
        )

        assert plan == []
