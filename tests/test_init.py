"""`mackup init <path>` writes the config once, then pulls the backup in."""

import io
import os
import shutil
import tempfile
import tomllib
import unittest
from unittest.mock import patch

import pytest

from mackup_ng import dirs, utils
from mackup_ng.main import main


class TestInit(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="mackup_init_home_")
        self.storage = tempfile.mkdtemp(prefix="mackup_init_store_")
        self._orig = {
            key: os.environ.get(key)
            for key in ("HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME")
        }
        os.environ["HOME"] = self.home
        os.environ["XDG_CONFIG_HOME"] = os.path.join(self.home, ".config")
        os.environ["XDG_DATA_HOME"] = os.path.join(self.home, ".local", "share")
        os.environ["XDG_STATE_HOME"] = os.path.join(self.home, ".local", "state")
        self.apps_dir = os.path.join(self.home, ".config", "mackup", "applications")
        os.makedirs(self.apps_dir, exist_ok=True)
        utils.FORCE_YES = True
        self.fetch_patcher = patch("mackup_ng.update.fetch_latest", return_value=None)
        self.fetch_patcher.start()
        self.addCleanup(self.fetch_patcher.stop)

    def tearDown(self):
        for key, value in self._orig.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(self.home, ignore_errors=True)
        shutil.rmtree(self.storage, ignore_errors=True)
        utils.FORCE_YES = False

    def _run(self, argv):
        buf = io.StringIO()
        with patch("sys.argv", argv), patch("sys.stdout", buf):
            main()
        return buf.getvalue()

    def _config(self):
        with open(dirs.config_file(), "rb") as handle:
            return tomllib.load(handle)

    def _write_app(self, name, body):
        with open(os.path.join(self.apps_dir, f"{name}.toml"), "w") as handle:
            handle.write(body)

    def test_init_writes_the_config_with_the_given_folder(self):
        target = os.path.join(self.storage, "Mackup")
        os.makedirs(target)

        self._run(["mackup", "init", target])

        assert self._config()["storage"]["backup_dir"] == target

    def test_a_folder_under_home_is_stored_relative_to_home(self):
        """The config folder is itself synced, so a $HOME-relative path keeps
        it portable to a machine whose home is somewhere else."""
        os.makedirs(os.path.join(self.home, "Sync", "Configs", "Mackup"))

        self._run(["mackup", "init", os.path.join(self.home, "Sync/Configs/Mackup")])

        assert self._config()["storage"]["backup_dir"] == "Sync/Configs/Mackup"

    def test_init_refuses_when_a_config_already_exists(self):
        os.makedirs(os.path.dirname(dirs.config_file()), exist_ok=True)
        with open(dirs.config_file(), "w") as handle:
            handle.write('[storage]\nbackup_dir = "already/here"\n')

        with pytest.raises(SystemExit) as caught:
            self._run(["mackup", "init", self.storage])

        message = str(caught.value)
        assert "already/here" in message, "the refusal must name the current folder"
        assert self._config()["storage"]["backup_dir"] == "already/here"

    def test_init_refuses_when_the_parent_folder_is_missing(self):
        missing = os.path.join(self.storage, "typo-not-here", "Mackup")

        with pytest.raises(SystemExit) as caught:
            self._run(["mackup", "init", missing])

        assert "typo-not-here" in str(caught.value)
        assert not os.path.exists(dirs.config_file())

    def test_init_pulls_the_backup_in_even_when_the_local_file_is_newer(self):
        """The whole point: a freshly installed application's default config
        has today's timestamp and must still lose to the backup."""
        target = os.path.join(self.storage, "Mackup")
        os.makedirs(target)
        backup = os.path.join(target, ".apprc")
        with open(backup, "w") as handle:
            handle.write("from-backup\n")
        os.utime(backup, (1000, 1000))
        local = os.path.join(self.home, ".apprc")
        with open(local, "w") as handle:
            handle.write("fresh-default\n")
        self._write_app("app", 'name = "App"\nfiles = [".apprc"]\n')

        self._run(["mackup", "init", target])

        with open(local) as handle:
            assert handle.read() == "from-backup\n"

    def test_dry_run_writes_neither_the_config_nor_any_file(self):
        target = os.path.join(self.storage, "Mackup")
        os.makedirs(target)
        with open(os.path.join(target, ".apprc"), "w") as handle:
            handle.write("from-backup\n")
        self._write_app("app", 'name = "App"\nfiles = [".apprc"]\n')

        self._run(["mackup", "-n", "init", target])

        assert not os.path.exists(dirs.config_file())
        assert not os.path.exists(os.path.join(self.home, ".apprc"))
