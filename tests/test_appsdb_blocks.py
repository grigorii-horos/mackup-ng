"""Tests for block / source_env parsing in ApplicationsDatabase."""

import io
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from mackup_ng import blocks
from mackup_ng.appsdb import ApplicationsDatabase


class TestAppsdbBlocks(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="mackup_adb_")
        self._orig = {k: os.environ.get(k) for k in ("HOME", "XDG_CONFIG_HOME")}
        os.environ["HOME"] = self.home
        os.environ["XDG_CONFIG_HOME"] = os.path.join(self.home, ".config")
        self.apps = os.path.join(self.home, ".mackup", "applications")
        os.makedirs(self.apps)

    def tearDown(self):
        for k, v in self._orig.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _write(self, name, body):
        with open(os.path.join(self.apps, f"{name}.toml"), "w") as f:
            f.write(body)

    def test_hybrid_top_level_block(self):
        # flat sync keys (name/files) + a top-level action sub-table ([chmod]).
        self._write(
            "openssh",
            'name = "SSH"\nfiles = [".ssh"]\n'
            '[chmod]\npath = "~/.ssh"\nmode = "700"\n',
        )
        db = ApplicationsDatabase()
        assert ".ssh" in db.get_files("openssh")
        cfg_blocks = db.get_blocks("openssh")
        assert len(cfg_blocks) == 1
        assert blocks.block_action(cfg_blocks[0]) == "chmod"
        assert db.app_has_sync("openssh")

    def test_top_level_block_precedes_block_array(self):
        self._write(
            "multi",
            '[run]\ncommands = ["a"]\n\n'
            '[[block]]\n[block.run]\ncommands = ["b"]\n',
        )
        cfg_blocks = ApplicationsDatabase().get_blocks("multi")
        assert [b["run"]["commands"] for b in cfg_blocks] == [["a"], ["b"]]

    def test_block_only_config(self):
        self._write(
            "10-linger",
            '[when]\nos = ["linux"]\ncommand = ["loginctl"]\n'
            '[run]\nscript = "sudo loginctl enable-linger $(id -un)"\n',
        )
        db = ApplicationsDatabase()
        assert "10-linger" in db.get_app_names()
        assert not db.app_has_sync("10-linger")
        block = db.get_blocks("10-linger")[0]
        assert blocks.block_action(block) == "run"
        assert db.get_conditions("10-linger")["os"] == ["linux"]

    def test_source_env(self):
        self._write(
            "ff",
            'name = "FF"\nsource_env = ["~/e"]\n'
            'files = ["${MACKUP_XDG_CONFIG}/ff"]\n',
        )
        db = ApplicationsDatabase()
        assert db.get_env_files("ff") == ["~/e"]

    def test_sync_only_has_no_blocks(self):
        self._write(
            "stock",
            'name = "Stock"\nfiles = [".stockrc"]\n',
        )
        db = ApplicationsDatabase()
        assert db.get_blocks("stock") == []
        assert db.app_has_sync("stock")

    def test_legacy_application_table_tolerated(self):
        # old [application] table still parses (name/files/source_env fall back)
        self._write(
            "legacy",
            '[application]\nname = "Legacy"\nfiles = [".legacyrc"]\n',
        )
        db = ApplicationsDatabase()
        assert db.get_name("legacy") == "Legacy"
        assert ".legacyrc" in db.get_files("legacy")


class TestConfigLevelWhen(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="mackup_cfgwhen_home_")
        self._orig_home = os.environ.get("HOME")
        self._orig_xdg = os.environ.get("XDG_CONFIG_HOME")
        os.environ["HOME"] = self.home
        os.environ["XDG_CONFIG_HOME"] = os.path.join(self.home, ".config")
        self.apps_dir = os.path.join(self.home, ".mackup", "applications")
        os.makedirs(self.apps_dir, exist_ok=True)

    def tearDown(self):
        for key, orig in (
            ("HOME", self._orig_home),
            ("XDG_CONFIG_HOME", self._orig_xdg),
        ):
            if orig is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = orig
        shutil.rmtree(self.home, ignore_errors=True)

    def _write_app(self, name, body):
        with open(os.path.join(self.apps_dir, f"{name}.toml"), "w") as handle:
            handle.write(f'name = "{name}"\n{body}')

    def test_conditions_are_recorded_and_kept_out_of_the_implicit_block(self):
        self._write_app(
            "gated",
            'files = [".gatedrc"]\n\n'
            '[when]\nos = ["android"]\n\n'
            '[chmod]\npath = "~/.gatedrc"\nmode = "600"\n',
        )
        db = ApplicationsDatabase()
        assert db.get_conditions("gated") == {"os": ["android"]}
        cfg_blocks = db.get_blocks("gated")
        assert len(cfg_blocks) == 1
        assert "when" not in cfg_blocks[0]
        assert "chmod" in cfg_blocks[0]

    def test_config_enabled_follows_the_conditions(self):
        self._write_app("gated", 'files = [".gatedrc"]\n\n[when]\nos = ["android"]\n')
        self._write_app("plain", 'files = [".plainrc"]\n')
        db = ApplicationsDatabase()
        with patch("mackup_ng.hooks.os_kind", return_value="linux"):
            assert db.config_enabled("gated") is False
            assert db.config_enabled("plain") is True
        with patch("mackup_ng.hooks.os_kind", return_value="android"):
            assert db.config_enabled("gated") is True


class TestConfigLevelWhenWarnings(unittest.TestCase):
    """Malformed / unrecognized top-level [when] warns at load time.

    The real-world slip: ``files = [...]`` written after the ``[when]``
    header parses as ``when.files`` in TOML, silently declaring no synced
    files at all. Nothing used to warn about this.
    """

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="mackup_cfgwhen_warn_home_")
        self._orig_home = os.environ.get("HOME")
        self._orig_xdg = os.environ.get("XDG_CONFIG_HOME")
        os.environ["HOME"] = self.home
        os.environ["XDG_CONFIG_HOME"] = os.path.join(self.home, ".config")
        self.apps_dir = os.path.join(self.home, ".mackup", "applications")
        os.makedirs(self.apps_dir, exist_ok=True)

    def tearDown(self):
        for key, orig in (
            ("HOME", self._orig_home),
            ("XDG_CONFIG_HOME", self._orig_xdg),
        ):
            if orig is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = orig
        shutil.rmtree(self.home, ignore_errors=True)

    def _write_app(self, name, body):
        with open(os.path.join(self.apps_dir, f"{name}.toml"), "w") as handle:
            handle.write(f'name = "{name}"\n{body}')

    def test_files_after_when_header_warns_and_names_the_key(self):
        # The exact real-world slip: `files = [...]` ends up as when.files
        # because it comes after the [when] table header in the TOML source.
        self._write_app(
            "slipped",
            '[when]\nos = ["android"]\nfiles = [".slippedrc"]\n',
        )
        buffer = io.StringIO()
        with patch("sys.stdout", buffer):
            ApplicationsDatabase()
        output = buffer.getvalue()
        assert "slipped" in output
        assert "unrecognized [when] key(s): files" in output

    def test_when_as_a_string_warns_and_is_treated_as_no_conditions(self):
        self._write_app("stringly", 'when = "android"\n')
        buffer = io.StringIO()
        with patch("sys.stdout", buffer):
            db = ApplicationsDatabase()
        output = buffer.getvalue()
        assert "stringly" in output
        assert "top-level [when] must be a table, ignoring" in output
        assert db.get_conditions("stringly") == {}
        assert db.config_enabled("stringly") is True

    def test_only_recognized_keys_warns_about_nothing(self):
        self._write_app(
            "clean",
            '[when]\nos = ["linux"]\nmarker = ["m"]\nnot_marker = ["n"]\n',
        )
        buffer = io.StringIO()
        with patch("sys.stdout", buffer):
            ApplicationsDatabase()
        assert buffer.getvalue() == ""

    def test_existing_condition_keys_across_the_suite_do_not_warn(self):
        # os / marker / not_marker are the only keys existing configs in this
        # suite use in a [when] table; confirm none of them starts warning.
        self._write_app(
            "combo",
            '[when]\nos = ["linux"]\nmarker = ["a"]\nnot_marker = ["b"]\n'
            'command = ["true"]\ngui = false\nexists = ["/"]\n'
            'not_exists = ["/no/such/path"]\nenv = ["HOME"]\narch = ["x86_64"]\n',
        )
        buffer = io.StringIO()
        with patch("sys.stdout", buffer):
            ApplicationsDatabase()
        assert buffer.getvalue() == ""


if __name__ == "__main__":
    unittest.main()
