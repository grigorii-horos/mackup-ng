"""Tests for marker definitions (package + local) and XDG state migration."""

import os
import tempfile
import unittest

from mackup_ng import hooks


class TestMarkers(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="mackup_markers_home_")
        self._orig = {k: os.environ.get(k) for k in ("HOME", "XDG_STATE_HOME")}
        os.environ["HOME"] = self.home
        os.environ["XDG_STATE_HOME"] = os.path.join(self.home, ".local", "state")
        self.local_defs = os.path.join(self.home, ".mackup", "markers")
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
        """A ~/.mackup/markers.d/<name>.cfg overrides the built-in of that name."""
        with open(os.path.join(self.local_defs, "backup.toml"), "w") as handle:
            handle.write('[marker]\nname = "my override"\n')
        with open(os.path.join(self.local_defs, "eink.toml"), "w") as handle:
            handle.write('[marker]\nname = "e-ink"\n')
        defs = hooks.load_marker_defs()
        assert defs["backup"]["name"] == "my override"
        assert defs["eink"]["name"] == "e-ink"

    def test_state_in_xdg(self):
        """set_marker writes into $XDG_STATE_HOME/mackup/markers, not ~/.mackup."""
        hooks.set_marker("eink")
        flag = os.path.join(
            self.home, ".local", "state", "mackup", "markers", "eink",
        )
        assert os.path.isfile(flag)
        assert hooks.has_marker("eink")
        assert not os.path.exists(os.path.join(self.home, ".mackup", "markers"))

    def test_legacy_state_migrated(self):
        """Pre-XDG flags in ~/.mackup/markers/ migrate into the XDG state dir."""
        legacy = os.path.join(self.home, ".mackup", "markers")
        os.makedirs(legacy, exist_ok=True)
        open(os.path.join(legacy, "backup"), "a").close()
        assert hooks.has_marker("backup")  # triggers migration
        assert not os.path.isdir(legacy)  # legacy dir removed once empty
        assert os.path.isfile(
            os.path.join(self.home, ".local", "state", "mackup", "markers", "backup"),
        )


if __name__ == "__main__":
    unittest.main()
