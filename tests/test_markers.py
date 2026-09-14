"""Tests for marker definitions (package + local) and marker STATE on XDG."""

import os
import tempfile
import unittest

from mackup_ng import hooks


class TestMarkers(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="mackup_markers_home_")
        self._orig = {
            k: os.environ.get(k) for k in ("HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME")
        }
        os.environ["HOME"] = self.home
        os.environ["XDG_CONFIG_HOME"] = os.path.join(self.home, ".config")
        os.environ["XDG_STATE_HOME"] = os.path.join(self.home, ".local", "state")
        self.local_defs = os.path.join(self.home, ".config", "mackup", "markers")
        os.makedirs(self.local_defs, exist_ok=True)

    def tearDown(self):
        for key, value in self._orig.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_builtin_defs_loaded(self):
        """Built-in marker defs ship in the package and load by name."""
        defs = hooks.load_marker_defs()
        assert "backup" in defs
        assert "no-dconf" in defs
        assert defs["backup"]["name"]
        assert defs["backup"]["order"] == 10

    def test_local_def_overrides_builtin(self):
        """A local <name>.toml overrides the built-in of that name."""
        with open(os.path.join(self.local_defs, "backup.toml"), "w") as handle:
            handle.write('[marker]\nname = "my override"\n')
        with open(os.path.join(self.local_defs, "eink.toml"), "w") as handle:
            handle.write('[marker]\nname = "e-ink"\n')
        defs = hooks.load_marker_defs()
        assert defs["backup"]["name"] == "my override"
        assert defs["eink"]["name"] == "e-ink"

    def test_state_in_xdg(self):
        """set_marker writes into $XDG_STATE_HOME/mackup/markers, not $XDG_CONFIG_HOME."""
        hooks.set_marker("eink")
        flag = os.path.join(
            self.home,
            ".local",
            "state",
            "mackup",
            "markers",
            "eink",
        )
        assert os.path.isfile(flag)
        assert hooks.has_marker("eink")
        assert not os.path.exists(os.path.join(self.home, ".mackup"))


def test_hook_env_exposes_the_three_xdg_roots(tmp_path, monkeypatch):
    """The MACKUP_* contract names each XDG root explicitly."""
    monkeypatch.setenv("HOME", str(tmp_path))
    for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME"):
        monkeypatch.delenv(var, raising=False)

    from mackup_ng import dirs, hooks

    env = hooks.hook_env("pre")

    assert env["MACKUP_CONFIG_DIR"] == dirs.config_dir()
    assert env["MACKUP_DATA_DIR"] == dirs.data_dir()
    assert env["MACKUP_STATE_DIR"] == dirs.state_dir()
    assert env["MACKUP_MARKERS_DIR"] == dirs.markers_state_dir()
    assert env["MACKUP_DCONF_BACKUP_DIR"] == dirs.dconf_backup_dir()


def test_no_legacy_marker_migration(tmp_path, monkeypatch):
    """Flags left in the pre-XDG directory are not resurrected."""
    monkeypatch.setenv("HOME", str(tmp_path))
    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME"):
        monkeypatch.delenv(var, raising=False)
    legacy = tmp_path / ".mackup" / "markers"
    legacy.mkdir(parents=True)
    (legacy / "backup").touch()

    from mackup_ng import hooks

    assert hooks.has_marker("backup") is False
    assert (legacy / "backup").exists()


if __name__ == "__main__":
    unittest.main()
