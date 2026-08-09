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
