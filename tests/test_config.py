import os
import os.path
import shutil
import tempfile
import unittest
from pathlib import Path

import pytest

from mackup_ng import dirs
from mackup_ng.config import Config, ConfigError


class TestConfig(unittest.TestCase):
    def setUp(self):
        realpath = os.path.dirname(os.path.realpath(__file__))
        os.environ["HOME"] = os.path.join(realpath, "fixtures")
        for key in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME"):
            os.environ.pop(key, None)

    def _config_in_temp_home(self, body):
        """Write `body` to the default config location under a throwaway $HOME.

        Uses its own temporary $HOME rather than the shared fixtures tree: an
        interrupted run must not leave a stray config.toml behind that a later
        run would silently pick up.
        """
        home = tempfile.mkdtemp(prefix="mackup_cfg_home_")
        original_home = os.environ["HOME"]
        os.environ["HOME"] = home
        config_path = Path(dirs.config_file())
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(body)

        def restore():
            os.environ["HOME"] = original_home
            shutil.rmtree(home, ignore_errors=True)

        return home, restore

    def test_config_default_location(self):
        home, restore = self._config_in_temp_home(
            '[storage]\nbackup_dir = "some/where/Mackup"\n',
        )
        try:
            assert Config().fullpath == os.path.join(home, "some/where/Mackup")
        finally:
            restore()

    def test_backup_dir_relative_is_resolved_against_home(self):
        cfg = Config("mackup-backup_dir-relative.toml")

        assert cfg.fullpath == os.path.join(
            os.environ["HOME"],
            "some/relative/folder",
        )
        assert cfg.apps_to_sync == {"sabnzbd", "sublime-text-3", "x11"}

    def test_backup_dir_absolute_is_used_as_given(self):
        cfg = Config("mackup-backup_dir-absolute.toml")

        assert cfg.fullpath == "/some/absolute/folder"
        assert cfg.apps_to_ignore == {"subversion", "sequel-pro"}

    def test_missing_backup_dir_is_an_error(self):
        """There is no sensible default storage location, so say so loudly."""
        with pytest.raises(ConfigError, match="backup_dir"):
            Config("mackup-no-backup_dir.toml")

    def test_empty_config_is_an_error(self):
        with pytest.raises(ConfigError, match="backup_dir"):
            Config("mackup-empty.toml")

    def test_backup_dir_must_be_a_string(self):
        _, restore = self._config_in_temp_home("[storage]\nbackup_dir = 42\n")
        try:
            with pytest.raises(ConfigError, match="backup_dir"):
                Config()
        finally:
            restore()

    def test_config_apps_to_ignore(self):
        cfg = Config("mackup-apps_to_ignore.toml")

        assert cfg.apps_to_ignore == {"subversion", "sequel-pro", "sabnzbd"}
        assert cfg.apps_to_sync == set()

    def test_config_apps_to_sync(self):
        cfg = Config("mackup-apps_to_sync.toml")

        assert cfg.apps_to_sync == {"sabnzbd", "sublime-text-3", "x11"}
        assert cfg.apps_to_ignore == set()

    def test_config_apps_to_ignore_and_sync(self):
        cfg = Config("mackup-apps_to_ignore_and_sync.toml")

        assert cfg.apps_to_ignore == {"subversion", "sequel-pro", "sabnzbd"}
        assert cfg.apps_to_sync == {"sabnzbd", "sublime-text-3", "x11", "vim"}

    def test_engine_path_and_directory_are_gone(self):
        """The three settings the cloud engines needed no longer exist."""
        cfg = Config("mackup-backup_dir-relative.toml")

        assert not hasattr(cfg, "engine")
        assert not hasattr(cfg, "path")
        assert not hasattr(cfg, "directory")

    def test_old_engine_keys_warn_rather_than_resolving_silently(self):
        """A pre-merge config must fail loudly, not sync to the wrong folder.

        `path = "Sync/Configs"` + `directory = "Mackup"` used to mean
        ~/Sync/Configs/Mackup. Reusing either name would have pointed the
        sync one level up without a word; `backup_dir` makes it an error.
        """
        _, restore = self._config_in_temp_home(
            '[storage]\nengine = "file_system"\n'
            'path = "Sync/Configs"\ndirectory = "Mackup"\n',
        )
        try:
            with pytest.raises(ConfigError, match="backup_dir"):
                Config()
        finally:
            restore()
