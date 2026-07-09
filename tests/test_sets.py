"""Tests for declarative config sets (mackup_ng.sets)."""

import os
import tempfile
import unittest

from mackup_ng import sets


class TestSets(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="mackup_sets_home_")
        self._orig_home = os.environ.get("HOME")
        self._orig_xdg = os.environ.get("XDG_CONFIG_HOME")
        os.environ["HOME"] = self.home
        os.environ["XDG_CONFIG_HOME"] = os.path.join(self.home, ".config")
        self.mackup = os.path.join(self.home, ".mackup")
        self.sets_dir = os.path.join(self.mackup, "sets.d")
        self.markers_dir = os.path.join(self.mackup, "markers")
        self.state_dir = os.path.join(self.mackup, "state")
        for path in (self.sets_dir, self.markers_dir, self.state_dir):
            os.makedirs(path, exist_ok=True)

    def tearDown(self):
        if self._orig_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._orig_home
        if self._orig_xdg is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = self._orig_xdg

    def _write_set(self, name, content):
        path = os.path.join(self.sets_dir, name)
        with open(path, "w") as handle:
            handle.write(content)
        return path

    def test_run_script_executes(self):
        """A [[run]] inline script runs with the MACKUP_* env."""
        path = self._write_set(
            "runner.toml",
            '[[run]]\nscript = "touch \\"$MACKUP_STATE_DIR/ran\\""\n',
        )
        sets.apply_file(path)
        assert os.path.isfile(os.path.join(self.state_dir, "ran"))

    def test_run_skipped_by_marker(self):
        """skip_if_marker prevents the run from executing."""
        open(os.path.join(self.markers_dir, "no-run"), "a").close()
        path = self._write_set(
            "gated.toml",
            'skip_if_marker = "no-run"\n'
            '[[run]]\nscript = "touch \\"$MACKUP_STATE_DIR/ran\\""\n',
        )
        sets.apply_file(path)
        assert not os.path.exists(os.path.join(self.state_dir, "ran"))

    def test_run_skipped_missing_command(self):
        """require_command with an absent binary skips the run."""
        path = self._write_set(
            "needs.toml",
            '[[run]]\n'
            'require_command = "definitely-not-a-real-binary-xyz"\n'
            'script = "touch \\"$MACKUP_STATE_DIR/ran\\""\n',
        )
        sets.apply_file(path)
        assert not os.path.exists(os.path.join(self.state_dir, "ran"))

    def test_run_with_present_command(self):
        """require_command that exists lets the run proceed."""
        path = self._write_set(
            "hascmd.toml",
            '[[run]]\n'
            'require_command = "sh"\n'
            'script = "touch \\"$MACKUP_STATE_DIR/ran\\""\n',
        )
        sets.apply_file(path)
        assert os.path.isfile(os.path.join(self.state_dir, "ran"))

    def test_xml_mutate_sets_child(self):
        """mutate_xml writes a child element into the target XML."""
        xml_path = os.path.join(self.home, "config.xml")
        with open(xml_path, "w") as handle:
            handle.write("<configuration><options><a>0</a></options></configuration>")
        path = self._write_set(
            "xml.toml",
            f'files = ["{xml_path}"]\n'
            "[[mutate_xml]]\n"
            'select = ["options"]\n'
            "create_missing = true\n"
            'set_child = { a = "1", b = "2" }\n',
        )
        sets.apply_file(path)
        content = open(xml_path).read()
        assert "<a>1</a>" in content
        assert "<b>2</b>" in content

    def _dropin(self, svc, name):
        return os.path.join(
            self.home, ".config", "systemd", "user",
            f"{svc}.service.d", f"{name}.conf",
        )

    def test_systemd_dropin_written_on_linux(self):
        """A default (linux) systemd_dropin block is written."""
        if sets.hooks.os_kind() != "linux":
            self.skipTest("linux-only")
        path = self._write_set(
            "drop.toml",
            "[[systemd_dropin]]\n"
            'service = "dummy"\n'
            'name = "t"\n'
            'Environment = ["X=1"]\n',
        )
        sets.apply_file(path)
        assert os.path.isfile(self._dropin("dummy", "t"))

    def test_systemd_dropin_skipped_wrong_os(self):
        """A systemd_dropin block with a non-matching require_os is skipped."""
        other = "macos" if sets.hooks.os_kind() != "macos" else "linux"
        path = self._write_set(
            "drop.toml",
            "[[systemd_dropin]]\n"
            f'require_os = "{other}"\n'
            'service = "dummy"\n'
            'name = "t"\n'
            'Environment = ["X=1"]\n',
        )
        sets.apply_file(path)
        assert not os.path.exists(self._dropin("dummy", "t"))

    def test_before_after_run_around_change(self):
        """before/after shell commands run when a change is applied."""
        if sets.hooks.os_kind() != "linux":
            self.skipTest("linux-only (uses systemd_dropin to force a change)")
        path = self._write_set(
            "ba.toml",
            'before = "touch \\"$MACKUP_STATE_DIR/before\\""\n'
            'after = "touch \\"$MACKUP_STATE_DIR/after\\""\n'
            "[[systemd_dropin]]\n"
            'service = "dummy"\n'
            'name = "t"\n'
            'Environment = ["X=1"]\n',
        )
        sets.apply_file(path)
        assert os.path.isfile(os.path.join(self.state_dir, "before"))
        assert os.path.isfile(os.path.join(self.state_dir, "after"))

    def test_xml_mutate_idempotent(self):
        """A second apply makes no change (byte-identical file)."""
        xml_path = os.path.join(self.home, "config.xml")
        with open(xml_path, "w") as handle:
            handle.write("<configuration><options><a>0</a></options></configuration>")
        path = self._write_set(
            "xml.toml",
            f'files = ["{xml_path}"]\n'
            "[[mutate_xml]]\n"
            'select = ["options"]\n'
            'set_child = { a = "1" }\n',
        )
        sets.apply_file(path)
        first = open(xml_path, "rb").read()
        sets.apply_file(path)
        second = open(xml_path, "rb").read()
        assert first == second


if __name__ == "__main__":
    unittest.main()
