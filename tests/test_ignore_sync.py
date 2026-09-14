"""Ignored names stay put: sync carries them in neither direction."""

import io
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from mackup_ng import ignore, utils
from mackup_ng.main import main

CONFLICT = "notes.sync-conflict-20260824-103000-ABCDEFG.md"


class TestIgnoredDuringSync(unittest.TestCase):
    def setUp(self):
        self.test_home = tempfile.mkdtemp(prefix="mackup_ign_home_")
        self.test_storage = tempfile.mkdtemp(prefix="mackup_ign_storage_")
        self.mackup_folder = os.path.join(self.test_storage, "Mackup")
        os.makedirs(self.mackup_folder, exist_ok=True)

        self._orig = {
            key: os.environ.get(key)
            for key in ("HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME")
        }
        os.environ["HOME"] = self.test_home
        os.environ["XDG_CONFIG_HOME"] = os.path.join(self.test_home, ".config")
        os.environ["XDG_CACHE_HOME"] = os.path.join(self.test_home, ".cache")
        os.environ["XDG_STATE_HOME"] = os.path.join(self.test_home, ".local", "state")
        ignore.load_globs.cache_clear()
        self.addCleanup(ignore.load_globs.cache_clear)

        self.config_path = os.path.join(
            self.test_home, ".config", "mackup", "config.toml",
        )
        os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
        self._write_config(["notes"])
        self.apps_dir = os.path.join(self.test_home, ".mackup", "applications")
        os.makedirs(self.apps_dir, exist_ok=True)
        self.write_app("notes", 'files = [".notes"]\n')

        self.local_dir = os.path.join(self.test_home, ".notes")
        self.backup_dir = os.path.join(self.mackup_folder, ".notes")
        os.makedirs(self.local_dir, exist_ok=True)

        self.fetch_patcher = patch("mackup_ng.update.fetch_latest", return_value=None)
        self.fetch_patcher.start()
        self.addCleanup(self.fetch_patcher.stop)
        utils.FORCE_YES = True

    def tearDown(self):
        for key, value in self._orig.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(self.test_home, ignore_errors=True)
        shutil.rmtree(self.test_storage, ignore_errors=True)
        utils.FORCE_YES = False

    def _write_config(self, apps):
        entries = ", ".join(f'"{app}"' for app in apps)
        with open(self.config_path, "w") as handle:
            handle.write(
                '[storage]\nengine = "file_system"\n'
                f'path = "{self.test_storage}"\ndirectory = "Mackup"\n\n'
                f"[applications]\nsync = [{entries}]\n",
            )

    def write_app(self, name, body):
        with open(os.path.join(self.apps_dir, f"{name}.toml"), "w") as handle:
            handle.write(f'name = "{name}"\n{body}')

    def write(self, path, text, mtime=None):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as handle:
            handle.write(text)
        if mtime is not None:
            os.utime(path, (mtime, mtime))

    def sync(self):
        with patch("sys.argv", ["mackup", "sync"]):
            main()

    def test_a_local_conflict_copy_is_not_backed_up(self):
        self.write(os.path.join(self.local_dir, "notes.md"), "real\n")
        self.write(os.path.join(self.local_dir, CONFLICT), "conflicted\n")

        self.sync()

        assert os.path.exists(os.path.join(self.backup_dir, "notes.md"))
        assert not os.path.exists(os.path.join(self.backup_dir, CONFLICT))

    def test_a_backed_up_conflict_copy_is_not_restored(self):
        self.write(os.path.join(self.backup_dir, "notes.md"), "real\n")
        self.write(os.path.join(self.backup_dir, CONFLICT), "conflicted\n")

        self.sync()

        assert os.path.exists(os.path.join(self.local_dir, "notes.md"))
        assert not os.path.exists(os.path.join(self.local_dir, CONFLICT))

    def test_a_local_conflict_copy_is_left_alone(self):
        conflict = os.path.join(self.local_dir, CONFLICT)
        self.write(os.path.join(self.local_dir, "notes.md"), "real\n")
        self.write(conflict, "conflicted\n")

        self.sync()

        assert os.path.exists(conflict)

    def test_a_fresh_conflict_copy_does_not_make_its_side_win(self):
        # The backup holds the real edit; the local side holds an older copy
        # plus a conflict file written just now. Without ignoring it, the
        # local tree looks newer and the real edit never lands.
        self.write(os.path.join(self.backup_dir, "notes.md"), "new\n", mtime=5000)
        self.write(os.path.join(self.local_dir, "notes.md"), "old\n", mtime=1000)
        self.write(os.path.join(self.local_dir, CONFLICT), "conflicted\n")

        self.sync()

        with open(os.path.join(self.local_dir, "notes.md")) as handle:
            assert handle.read() == "new\n"

    def test_info_does_not_count_a_conflict_copy_as_a_difference(self):
        self.write(os.path.join(self.local_dir, "notes.md"), "real\n")
        self.sync()
        self.write(os.path.join(self.local_dir, CONFLICT), "conflicted\n")

        buffer = io.StringIO()
        with (
            patch("sys.stdout", buffer),
            patch("sys.argv", ["mackup", "info", ".notes"]),
        ):
            main()

        assert "State: in sync" in buffer.getvalue()

    def test_a_config_ignores_its_own_patterns(self):
        self.write_app("notes", 'files = [".notes"]\nignore = ["*.bak"]\n')
        self.write(os.path.join(self.local_dir, "notes.md"), "real\n")
        self.write(os.path.join(self.local_dir, "notes.md.bak"), "stale\n")

        self.sync()

        assert os.path.exists(os.path.join(self.backup_dir, "notes.md"))
        assert not os.path.exists(os.path.join(self.backup_dir, "notes.md.bak"))

    def test_one_config_ignore_does_not_reach_another_config(self):
        self.write_app("notes", 'files = [".notes"]\nignore = ["*.bak"]\n')
        self.write_app("other", 'files = [".other"]\n')
        self._write_config(["notes", "other"])
        self.write(os.path.join(self.test_home, ".other", "keep.md.bak"), "keep\n")

        self.sync()

        assert os.path.exists(os.path.join(self.mackup_folder, ".other", "keep.md.bak"))


if __name__ == "__main__":
    unittest.main()
