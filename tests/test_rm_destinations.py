"""Removal is scoped to one destination, not the whole fanout group."""

import io
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from mackup_ng import utils
from mackup_ng.main import main


class TestRemoveDestination(unittest.TestCase):
    def setUp(self):
        self.test_home = tempfile.mkdtemp(prefix="mackup_rmdest_home_")
        self.test_storage = tempfile.mkdtemp(prefix="mackup_rmdest_storage_")
        self.mackup_folder = os.path.join(self.test_storage, "Mackup")
        os.makedirs(self.mackup_folder, exist_ok=True)
        self._orig_home = os.environ.get("HOME")
        self._orig_xdg = os.environ.get("XDG_CONFIG_HOME")
        os.environ["HOME"] = self.test_home
        os.environ["XDG_CONFIG_HOME"] = os.path.join(self.test_home, ".config")

        with open(os.path.join(self.test_home, ".mackup.cfg"), "w") as handle:
            handle.write(
                "[storage]\nengine = file_system\n"
                f"path = {self.test_storage}\ndirectory = Mackup\n\n"
                "[applications_to_sync]\nfanout\n",
            )
        apps_dir = os.path.join(self.test_home, ".mackup", "applications")
        os.makedirs(apps_dir, exist_ok=True)
        with open(os.path.join(apps_dir, "fanout.toml"), "w") as handle:
            handle.write(
                'name = "fanout"\n\n[mapped_files]\n'
                '".work.rc" = ".shared.rc"\n'
                '".home.rc" = ".shared.rc"\n',
            )
        with open(os.path.join(self.mackup_folder, ".shared.rc"), "w") as handle:
            handle.write("shared=1\n")
        utils.FORCE_YES = True

    def tearDown(self):
        for key, orig in (
            ("HOME", self._orig_home),
            ("XDG_CONFIG_HOME", self._orig_xdg),
        ):
            if orig is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = orig
        shutil.rmtree(self.test_home, ignore_errors=True)
        shutil.rmtree(self.test_storage, ignore_errors=True)
        utils.FORCE_YES = False

    def test_rm_one_destination_keeps_sibling_and_source(self):
        with patch("sys.argv", ["mackup", "sync"]):
            main()
        buffer = io.StringIO()
        with patch("sys.stdout", buffer), patch(
            "sys.argv", ["mackup", "rm", ".work.rc"],
        ):
            main()

        assert not os.path.exists(os.path.join(self.test_home, ".work.rc"))
        assert os.path.exists(os.path.join(self.test_home, ".home.rc"))
        assert os.path.exists(os.path.join(self.mackup_folder, ".shared.rc"))
        assert "still feeds 1 destination" in buffer.getvalue()

    def test_rm_last_destination_removes_the_source(self):
        with patch("sys.argv", ["mackup", "sync"]):
            main()
        for name in (".work.rc", ".home.rc"):
            with patch("sys.argv", ["mackup", "rm", name]):
                main()

        assert not os.path.exists(os.path.join(self.mackup_folder, ".shared.rc"))

    def test_rm_both_destinations_in_one_invocation_removes_the_source(self):
        with patch("sys.argv", ["mackup", "sync"]):
            main()
        with patch(
            "sys.argv", ["mackup", "rm", ".work.rc", ".home.rc"],
        ):
            main()

        assert not os.path.exists(os.path.join(self.test_home, ".work.rc"))
        assert not os.path.exists(os.path.join(self.test_home, ".home.rc"))
        assert not os.path.exists(os.path.join(self.mackup_folder, ".shared.rc"))

    def test_tombstone_records_the_destination(self):
        with patch("sys.argv", ["mackup", "sync"]):
            main()
        with patch("sys.argv", ["mackup", "rm", ".work.rc"]):
            main()

        with open(os.path.join(self.mackup_folder, ".mackup-deletions")) as handle:
            assert handle.read().split() == [".work.rc"]
