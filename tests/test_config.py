import os
import os.path
import unittest
from pathlib import Path

import pytest

from mackup_ng import dirs
from mackup_ng.config import Config, ConfigError
from mackup_ng.constants import (
    ENGINE_DROPBOX,
    ENGINE_FS,
    ENGINE_GDRIVE,
    ENGINE_ICLOUD,
)


class TestConfig(unittest.TestCase):
    def setUp(self):
        realpath = os.path.dirname(os.path.realpath(__file__))
        os.environ["HOME"] = os.path.join(realpath, "fixtures")
        for key in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME"):
            os.environ.pop(key, None)

    def test_config_default_location(self):
        config_path = Path(dirs.config_file())
        config_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            config_path.write_text('[storage]\ndirectory = "test_config_default"\n')
            assert Config().directory == "test_config_default"
        finally:
            config_path.unlink(missing_ok=True)

    def test_config_no_config(self):
        cfg = Config()

        assert cfg.engine == ENGINE_DROPBOX
        assert isinstance(cfg.path, str)
        assert cfg.directory == "Mackup"
        assert cfg.apps_to_ignore == set()
        assert cfg.apps_to_sync == set()

    def test_config_empty(self):
        cfg = Config("mackup-empty.toml")

        assert cfg.engine == ENGINE_DROPBOX
        assert cfg.directory == "Mackup"
        assert cfg.apps_to_ignore == set()
        assert cfg.apps_to_sync == set()

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

    def test_config_engine_dropbox(self):
        cfg = Config("mackup-engine-dropbox.toml")

        assert cfg.engine == ENGINE_DROPBOX
        assert cfg.directory == "some_weirld_name"

    def test_config_engine_file_system(self):
        cfg = Config("mackup-engine-file_system.toml")

        assert cfg.engine == ENGINE_FS
        assert cfg.path == os.path.join(os.environ["HOME"], "some/relative/folder")
        assert cfg.directory == "Mackup"
        assert cfg.fullpath == os.path.join(cfg.path, "Mackup")

    def test_config_engine_file_system_absolute(self):
        cfg = Config("mackup-engine-file_system-absolute.toml")

        assert cfg.engine == ENGINE_FS
        assert cfg.path == "/some/absolute/folder"
        assert cfg.directory == "custom_folder"

    def test_config_engine_file_system_no_path(self):
        with pytest.raises(ConfigError):
            Config("mackup-engine-file_system-no_path.toml")

    def test_config_engine_google_drive(self):
        cfg = Config("mackup-engine-google_drive.toml")

        assert cfg.engine == ENGINE_GDRIVE

    def test_config_engine_icloud(self):
        cfg = Config("mackup-engine-icloud.toml")

        assert cfg.engine == ENGINE_ICLOUD

    def test_config_engine_unknown(self):
        with pytest.raises(ConfigError):
            Config("mackup-engine-unknown.toml")
