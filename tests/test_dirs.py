"""XDG base resolution: one implementation, one set of fallback rules."""

import os
import unittest

from mackup_ng import dirs


class TestDirs(unittest.TestCase):
    def setUp(self):
        self._orig = {
            key: os.environ.get(key)
            for key in ("HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME")
        }
        os.environ["HOME"] = "/home/tester"
        for key in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME"):
            os.environ.pop(key, None)

    def tearDown(self):
        for key, orig in self._orig.items():
            if orig is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = orig

    def test_defaults_when_unset(self):
        assert dirs.config_dir() == "/home/tester/.config/mackup"
        assert dirs.data_dir() == "/home/tester/.local/share/mackup"
        assert dirs.state_dir() == "/home/tester/.local/state/mackup"

    def test_absolute_env_value_is_honoured(self):
        os.environ["XDG_CONFIG_HOME"] = "/elsewhere/cfg"
        assert dirs.config_dir() == "/elsewhere/cfg/mackup"

    def test_empty_env_value_falls_back(self):
        # appsdb.py used .get(var, default) and built a path from "/" here,
        # while ignore.py used `or default`. One rule now.
        os.environ["XDG_CONFIG_HOME"] = ""
        assert dirs.config_dir() == "/home/tester/.config/mackup"

    def test_relative_env_value_falls_back(self):
        # The XDG spec says a relative base must be ignored.
        os.environ["XDG_STATE_HOME"] = "relative/state"
        assert dirs.state_dir() == "/home/tester/.local/state/mackup"

    def test_derived_paths(self):
        assert dirs.config_file() == "/home/tester/.config/mackup/config.toml"
        assert dirs.custom_apps_dir() == "/home/tester/.config/mackup/applications"
        assert dirs.custom_ignores_dir() == "/home/tester/.config/mackup/ignores"
        assert dirs.custom_markers_dir() == "/home/tester/.config/mackup/markers"
        assert dirs.markers_state_dir() == "/home/tester/.local/state/mackup/markers"
        assert (
            dirs.dconf_backup_dir()
            == "/home/tester/.local/share/mackup/dconf-backup"
        )
