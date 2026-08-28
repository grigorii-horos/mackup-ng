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
            mackup=self.mackup,
            dry_run=False,
            verbose=False,
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

    def test_unusable_destination_parent_reports_error_without_aborting(self):
        """A file where a destination's parent directory belongs is an error."""
        source = os.path.join(self.mackup.mackup_folder, ".config/app/user.js")
        self._write(source, "from-backup", 3000)
        # ~/blocker is a regular file, so ~/blocker/user.js can never be created.
        self._write(os.path.join(self.home, "blocker"), "in the way", 1000)

        stats = self.profile.sync_group(
            ".config/app/user.js",
            ["blocker/user.js", ".config/home/user.js"],
        )

        assert stats["errors"] == 1
        assert stats["restored"] == 1
        with open(os.path.join(self.home, ".config/home/user.js")) as handle:
            assert handle.read() == "from-backup"

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
            mackup=self.mackup,
            dry_run=True,
            verbose=False,
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
            mackup=self.mackup,
            dry_run=False,
            verbose=False,
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


class TestSyncGroupTypeConflicts(unittest.TestCase):
    """A member that is a file where another is a directory must not abort."""

    def setUp(self):
        self.mackup = Mock(spec=Mackup)
        self.mackup.mackup_folder = tempfile.mkdtemp(prefix="mackup_gclash_backup_")
        self.home = tempfile.mkdtemp(prefix="mackup_gclash_home_")
        self._orig_home = os.environ.get("HOME")
        os.environ["HOME"] = self.home
        self.profile = ApplicationProfile(
            mackup=self.mackup,
            dry_run=False,
            verbose=False,
        )

    def tearDown(self):
        if self._orig_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._orig_home
        shutil.rmtree(self.home, ignore_errors=True)
        shutil.rmtree(self.mackup.mackup_folder, ignore_errors=True)

    def _write_file(self, path, content, mtime):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as handle:
            handle.write(content)
        os.utime(path, (mtime, mtime))

    def _write_dir(self, path, content, mtime):
        os.makedirs(path, exist_ok=True)
        self._write_file(os.path.join(path, "inside.txt"), content, mtime)
        os.utime(path, (mtime, mtime))

    def test_backup_directory_replaces_older_destination_file(self):
        backup = os.path.join(self.mackup.mackup_folder, ".cfgdir")
        dest = os.path.join(self.home, ".cfgdir")
        self._write_dir(backup, "from-backup", 5000)
        self._write_file(dest, "stale-local", 1000)

        stats = self.profile.sync_group(".cfgdir", [".cfgdir"])

        assert os.path.isdir(dest)
        with open(os.path.join(dest, "inside.txt")) as handle:
            assert handle.read() == "from-backup"
        assert stats["errors"] == 0
        assert stats["restored"] == 1

    def test_newer_destination_file_replaces_backup_directory(self):
        backup = os.path.join(self.mackup.mackup_folder, ".cfgdir")
        dest = os.path.join(self.home, ".cfgdir")
        self._write_dir(backup, "stale-backup", 1000)
        self._write_file(dest, "fresh-local", 5000)

        stats = self.profile.sync_group(".cfgdir", [".cfgdir"])

        assert os.path.isfile(backup)
        with open(backup) as handle:
            assert handle.read() == "fresh-local"
        assert stats["errors"] == 0
        assert stats["backed_up"] == 1

    def test_backup_file_replaces_older_destination_directory(self):
        backup = os.path.join(self.mackup.mackup_folder, ".cfgdir")
        dest = os.path.join(self.home, ".cfgdir")
        self._write_file(backup, "fresh-backup", 5000)
        self._write_dir(dest, "stale-local", 1000)

        stats = self.profile.sync_group(".cfgdir", [".cfgdir"])

        assert os.path.isfile(dest)
        with open(dest) as handle:
            assert handle.read() == "fresh-backup"
        assert stats["errors"] == 0
        assert stats["restored"] == 1

    def test_newer_destination_directory_replaces_backup_file(self):
        backup = os.path.join(self.mackup.mackup_folder, ".cfgdir")
        dest = os.path.join(self.home, ".cfgdir")
        self._write_file(backup, "stale-backup", 1000)
        self._write_dir(dest, "fresh-local", 5000)

        stats = self.profile.sync_group(".cfgdir", [".cfgdir"])

        assert os.path.isdir(backup)
        with open(os.path.join(backup, "inside.txt")) as handle:
            assert handle.read() == "fresh-local"
        assert stats["errors"] == 0
        assert stats["backed_up"] == 1

    def test_only_the_clashing_member_is_replaced_wholesale(self):
        """A same-type sibling still merges entry by entry."""
        backup = os.path.join(self.mackup.mackup_folder, ".cfgdir")
        clash = os.path.join(self.home, ".work.d")
        peer = os.path.join(self.home, ".home.d")
        self._write_dir(backup, "from-backup", 5000)
        self._write_file(clash, "stale-local", 1000)
        self._write_file(os.path.join(peer, "peer-only.txt"), "peer", 4000)

        stats = self.profile.sync_group(".cfgdir", [".work.d", ".home.d"])

        assert stats["errors"] == 0
        for root in (backup, clash, peer):
            with open(os.path.join(root, "inside.txt")) as handle:
                assert handle.read() == "from-backup"
            with open(os.path.join(root, "peer-only.txt")) as handle:
                assert handle.read() == "peer"


class TestSyncGroupDirectoryPermissionErrors(unittest.TestCase):
    def setUp(self):
        self.mackup = Mock(spec=Mackup)
        self.mackup.mackup_folder = tempfile.mkdtemp(prefix="mackup_gdir_perm_backup_")
        self.home = tempfile.mkdtemp(prefix="mackup_gdir_perm_home_")
        self._orig_home = os.environ.get("HOME")
        os.environ["HOME"] = self.home
        self.profile = ApplicationProfile(
            mackup=self.mackup,
            dry_run=False,
            verbose=False,
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
