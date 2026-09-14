"""Tests for the --config-file command line option."""

import os
import unittest

import pytest

from mackup_ng.config import Config, ConfigError
from mackup_ng.mackup import Mackup


class TestConfigFileOption(unittest.TestCase):
    def setUp(self):
        realpath = os.path.dirname(os.path.realpath(__file__))
        os.environ["HOME"] = os.path.join(realpath, "fixtures")

        # Clear environment variables that could interfere
        os.environ.pop("XDG_CONFIG_HOME", None)

    def test_config_with_relative_path(self):
        """Test that a relative path to config file works."""
        cfg = Config("mackup-apps_to_ignore.toml")

        assert cfg.apps_to_ignore == {"subversion", "sequel-pro", "sabnzbd"}

    def test_config_with_absolute_path(self):
        """Test that an absolute path to config file works."""
        abs_path = os.path.join(os.environ["HOME"], "mackup-apps_to_sync.toml")
        cfg = Config(abs_path)

        assert cfg.apps_to_sync == {"sabnzbd", "sublime-text-3", "x11"}

    def test_mackup_with_config_file(self):
        """Test that Mackup class accepts config_file parameter."""
        mckp = Mackup("mackup-backup_dir-absolute.toml")

        assert mckp.mackup_folder == "/some/absolute/folder"

    def test_mackup_without_a_config_anywhere_is_an_error(self):
        """Default discovery finds nothing here, and that is now fatal.

        There is no storage location to fall back to since the cloud engines
        were removed, so Mackup says so rather than guessing a folder.
        """
        with pytest.raises(ConfigError, match="backup_dir"):
            Mackup()

    def test_config_file_does_not_exist(self):
        """Test that specifying a non-existent config file raises an error."""
        with pytest.raises(SystemExit):
            Config("nonexistent-config-file.cfg")
