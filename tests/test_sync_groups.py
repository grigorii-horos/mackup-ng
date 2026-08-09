"""Tests for fanout group synchronization."""

import os
import shutil
import tempfile
import unittest
from unittest.mock import Mock

from mackup_ng.application import ApplicationProfile
from mackup_ng.mackup import Mackup


class TestSyncGroupFiles(unittest.TestCase):
    def setUp(self):
        self.mackup = Mock(spec=Mackup)
        self.mackup.mackup_folder = tempfile.mkdtemp(prefix="mackup_group_backup_")
        self.home = tempfile.mkdtemp(prefix="mackup_group_home_")
        self._orig_home = os.environ.get("HOME")
        os.environ["HOME"] = self.home
        self.profile = ApplicationProfile(
            mackup=self.mackup, files=set(), dry_run=False, verbose=False,
        )

    def tearDown(self):
        if self._orig_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._orig_home
        shutil.rmtree(self.home, ignore_errors=True)
        shutil.rmtree(self.mackup.mackup_folder, ignore_errors=True)

    def _write(self, path, content, mtime):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as handle:
            handle.write(content)
        os.utime(path, (mtime, mtime))

    def test_backup_source_fans_out_to_every_destination(self):
        source = os.path.join(self.mackup.mackup_folder, ".config/app/user.js")
        self._write(source, "from-backup", 3000)
        stats = self.profile.sync_group(
            ".config/app/user.js",
            [".config/work/user.js", ".config/home/user.js"],
        )
        for dest in (".config/work/user.js", ".config/home/user.js"):
            with open(os.path.join(self.home, dest)) as handle:
                assert handle.read() == "from-backup"
        assert stats["restored"] == 2

    def test_newest_destination_wins_over_the_whole_group(self):
        source = os.path.join(self.mackup.mackup_folder, ".config/app/user.js")
        work = os.path.join(self.home, ".config/work/user.js")
        personal = os.path.join(self.home, ".config/home/user.js")
        self._write(source, "old-backup", 1000)
        self._write(work, "newest", 5000)
        self._write(personal, "stale", 2000)

        stats = self.profile.sync_group(
            ".config/app/user.js",
            [".config/work/user.js", ".config/home/user.js"],
        )

        for path in (source, personal):
            with open(path) as handle:
                assert handle.read() == "newest"
        assert stats["backed_up"] == 1
        assert stats["restored"] == 1

    def test_group_with_no_existing_member_is_a_no_op(self):
        stats = self.profile.sync_group(".config/missing", [".config/nowhere"])
        assert not any(stats.values())
        assert not os.path.exists(os.path.join(self.home, ".config/nowhere"))

    def test_dry_run_reports_without_writing(self):
        source = os.path.join(self.mackup.mackup_folder, ".config/app/user.js")
        self._write(source, "from-backup", 3000)
        profile = ApplicationProfile(
            mackup=self.mackup, files=set(), dry_run=True, verbose=False,
        )
        stats = profile.sync_group(".config/app/user.js", [".config/work/user.js"])
        assert stats["restored"] == 1
        assert not os.path.exists(os.path.join(self.home, ".config/work/user.js"))


class TestSyncGroupDirectories(unittest.TestCase):
    def setUp(self):
        self.mackup = Mock(spec=Mackup)
        self.mackup.mackup_folder = tempfile.mkdtemp(prefix="mackup_gdir_backup_")
        self.home = tempfile.mkdtemp(prefix="mackup_gdir_home_")
        self._orig_home = os.environ.get("HOME")
        os.environ["HOME"] = self.home
        self.profile = ApplicationProfile(
            mackup=self.mackup, files=set(), dry_run=False, verbose=False,
        )

    def tearDown(self):
        if self._orig_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._orig_home
        shutil.rmtree(self.home, ignore_errors=True)
        shutil.rmtree(self.mackup.mackup_folder, ignore_errors=True)

    def _write(self, path, content, mtime):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as handle:
            handle.write(content)
        os.utime(path, (mtime, mtime))

    def test_entries_union_across_all_members(self):
        backup = os.path.join(self.mackup.mackup_folder, "profile")
        work = os.path.join(self.home, "work")
        personal = os.path.join(self.home, "personal")
        self._write(os.path.join(backup, "shared.txt"), "backup", 1000)
        self._write(os.path.join(work, "only-work.txt"), "work", 2000)
        self._write(os.path.join(personal, "shared.txt"), "newest", 5000)

        stats = self.profile.sync_group("profile", ["work", "personal"])

        for root in (backup, work, personal):
            with open(os.path.join(root, "shared.txt")) as handle:
                assert handle.read() == "newest"
            with open(os.path.join(root, "only-work.txt")) as handle:
                assert handle.read() == "work"
        assert stats["synchronized"] == 1

    def test_nested_entries_are_created_in_every_member(self):
        backup = os.path.join(self.mackup.mackup_folder, "profile")
        self._write(os.path.join(backup, "nested", "deep.txt"), "deep", 4000)

        self.profile.sync_group("profile", ["work", "personal"])

        for name in ("work", "personal"):
            nested = os.path.join(self.home, name, "nested", "deep.txt")
            with open(nested) as handle:
                assert handle.read() == "deep"

    def test_unchanged_group_reports_skipped(self):
        backup = os.path.join(self.mackup.mackup_folder, "profile")
        work = os.path.join(self.home, "work")
        self._write(os.path.join(backup, "same.txt"), "same", 4000)
        self._write(os.path.join(work, "same.txt"), "same", 4000)

        stats = self.profile.sync_group("profile", ["work"])

        assert stats["synchronized"] == 0
        assert stats["skipped"] == 1


class TestSyncGroupDirectoryPermissionErrors(unittest.TestCase):
    def setUp(self):
        self.mackup = Mock(spec=Mackup)
        self.mackup.mackup_folder = tempfile.mkdtemp(prefix="mackup_gdir_perm_backup_")
        self.home = tempfile.mkdtemp(prefix="mackup_gdir_perm_home_")
        self._orig_home = os.environ.get("HOME")
        os.environ["HOME"] = self.home
        self.profile = ApplicationProfile(
            mackup=self.mackup, files=set(), dry_run=False, verbose=False,
        )
        self.locked_parent = os.path.join(self.home, "locked")
        os.makedirs(self.locked_parent)

    def tearDown(self):
        os.chmod(self.locked_parent, 0o700)
        if self._orig_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._orig_home
        shutil.rmtree(self.home, ignore_errors=True)
        shutil.rmtree(self.mackup.mackup_folder, ignore_errors=True)

    def _write(self, path, content, mtime):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as handle:
            handle.write(content)
        os.utime(path, (mtime, mtime))

    @unittest.skipIf(os.geteuid() == 0, "root ignores permission bits")
    def test_unwritable_member_parent_reports_error_without_aborting(self):
        backup = os.path.join(self.mackup.mackup_folder, "profile")
        work = os.path.join(self.home, "work")
        self._write(os.path.join(backup, "shared.txt"), "backup", 1000)
        os.chmod(self.locked_parent, 0o500)

        stats = self.profile.sync_group("profile", ["work", "locked/dest"])

        with open(os.path.join(work, "shared.txt")) as handle:
            assert handle.read() == "backup"
        assert stats["errors"] > 0
        assert not os.path.exists(os.path.join(self.locked_parent, "dest"))
