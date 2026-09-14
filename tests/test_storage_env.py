"""The backup folder's parent must already exist; the folder itself is created.

With `storage.path` and `storage.directory` merged into a single
`storage.backup_dir`, the old guard ("the storage root must be a directory")
becomes "the folder *containing* the backup folder must be a directory". That
keeps the original intent: a typo in the config should be reported, not
silently turned into a deep new tree somewhere unexpected.
"""

import os
import shutil
import tempfile
import unittest

import pytest

from mackup_ng import utils
from mackup_ng.mackup import Mackup

from .conftest import write_config


class TestStorageEnvironment(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="mackup_storage_env_home_")
        self.storage = tempfile.mkdtemp(prefix="mackup_storage_env_store_")
        self._orig = {
            key: os.environ.get(key)
            for key in ("HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME")
        }
        os.environ["HOME"] = self.home
        os.environ["XDG_CONFIG_HOME"] = os.path.join(self.home, ".config")
        os.environ["XDG_DATA_HOME"] = os.path.join(self.home, ".local", "share")
        os.environ["XDG_STATE_HOME"] = os.path.join(self.home, ".local", "state")
        self.config_path = os.path.join(
            self.home,
            ".config",
            "mackup",
            "config.toml",
        )
        utils.FORCE_YES = True

    def tearDown(self):
        for key, value in self._orig.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(self.home, ignore_errors=True)
        shutil.rmtree(self.storage, ignore_errors=True)
        utils.FORCE_YES = False

    def test_missing_parent_of_backup_dir_is_reported(self):
        write_config(
            self.config_path,
            storage_path=os.path.join(self.storage, "typo-not-here"),
            directory="Mackup",
        )

        with pytest.raises(SystemExit) as excinfo:
            Mackup().check_for_usable_backup_env()

        assert "typo-not-here" in str(excinfo.value)

    def test_backup_dir_itself_is_created_when_its_parent_exists(self):
        write_config(self.config_path, storage_path=self.storage, directory="Mackup")
        expected = os.path.join(self.storage, "Mackup")
        assert not os.path.isdir(expected)

        Mackup().check_for_usable_backup_env()

        assert os.path.isdir(expected)
