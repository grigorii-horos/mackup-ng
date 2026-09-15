"""`mackup init` gives the backup side authority; ordinary sync does not.

Ordinary sync resolves every contest by picking the newest member, which is
the wrong rule for a first sync on a fresh machine: freshly installed
applications write their default configs with today's timestamp, so the
defaults would beat the real settings in the backup and then propagate to
every other machine.

A profile created with ``prefer_backup`` reports an infinite mtime for paths
inside the backup folder. That is the whole mechanism: the engine resolves
every contest through one comparison, so making the backup unbeatable there
makes it unbeatable everywhere — including the "not older than the winner"
skip, which can never fire against an infinite winner.

Each test pairs the two rules on the same scenario, so a change that quietly
drops the preference fails instead of passing on a technicality.
"""

import os
import shutil
import tempfile
import unittest
from unittest.mock import Mock

from mackup_ng.application import ApplicationProfile
from mackup_ng.mackup import Mackup

OLD = 1000.0
NEW = 9000.0


class TestPreferBackup(unittest.TestCase):
    def setUp(self):
        self.mackup = Mock(spec=Mackup)
        self.mackup.mackup_folder = tempfile.mkdtemp(prefix="mackup_prefer_backup_")
        self.home = tempfile.mkdtemp(prefix="mackup_prefer_home_")
        self._orig_home = os.environ.get("HOME")
        os.environ["HOME"] = self.home

    def tearDown(self):
        if self._orig_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._orig_home
        shutil.rmtree(self.home, ignore_errors=True)
        shutil.rmtree(self.mackup.mackup_folder, ignore_errors=True)

    def _profile(self, *, prefer_backup):
        return ApplicationProfile(
            mackup=self.mackup,
            dry_run=False,
            verbose=False,
            prefer_backup=prefer_backup,
        )

    def _write(self, path, content, mtime):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as handle:
            handle.write(content)
        os.utime(path, (mtime, mtime))

    def _read(self, path):
        with open(path) as handle:
            return handle.read()

    def _stage_contested_file(self, relative):
        """A stale backup file and a newer local one claiming the same path."""
        self._write(
            os.path.join(self.mackup.mackup_folder, relative),
            "from-backup",
            OLD,
        )
        local = os.path.join(self.home, relative)
        self._write(local, "from-local", NEW)
        return local

    def test_a_newer_local_file_loses_to_the_backup(self):
        local = self._stage_contested_file(".newerrc")

        self._profile(prefer_backup=True).sync_group(".newerrc", [".newerrc"])

        assert self._read(local) == "from-backup"

    def test_the_same_newer_local_file_wins_under_ordinary_sync(self):
        """The other half of the pair: without the flag, newest still wins."""
        local = self._stage_contested_file(".newerrc")

        self._profile(prefer_backup=False).sync_group(".newerrc", [".newerrc"])

        assert self._read(local) == "from-local"

    def test_a_newer_local_entry_inside_a_directory_loses_to_the_backup(self):
        """Directories merge entry by entry, so each entry needs the rule too."""
        backup_dir = os.path.join(self.mackup.mackup_folder, ".confdir")
        local_dir = os.path.join(self.home, ".confdir")
        self._write(os.path.join(backup_dir, "shared.ini"), "from-backup", OLD)
        self._write(os.path.join(local_dir, "shared.ini"), "from-local", NEW)
        self._write(os.path.join(local_dir, "only-local.ini"), "keep-me", NEW)

        self._profile(prefer_backup=True).sync_group(".confdir", [".confdir"])

        assert self._read(os.path.join(local_dir, "shared.ini")) == "from-backup"
        # Still a union merge: an entry the backup never held survives.
        assert self._read(os.path.join(local_dir, "only-local.ini")) == "keep-me"

    def test_a_newer_local_directory_loses_to_a_backup_file(self):
        """A file-versus-directory clash resolves in the backup's favour too."""
        self._write(
            os.path.join(self.mackup.mackup_folder, ".clash"),
            "from-backup",
            OLD,
        )
        local = os.path.join(self.home, ".clash")
        self._write(os.path.join(local, "inside.txt"), "from-local", NEW)

        self._profile(prefer_backup=True).sync_group(".clash", [".clash"])

        assert os.path.isfile(local)
        assert self._read(local) == "from-backup"

    def test_a_path_only_the_local_side_has_is_still_backed_up(self):
        """Preferring the backup is not ignoring the local side: a path the
        backup does not hold at all must still reach it."""
        self._write(os.path.join(self.home, ".localonly"), "from-local", NEW)

        self._profile(prefer_backup=True).sync_group(".localonly", [".localonly"])

        backup = os.path.join(self.mackup.mackup_folder, ".localonly")
        assert self._read(backup) == "from-local"
