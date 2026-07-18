"""Tests for block / source_env parsing in ApplicationsDatabase."""

import os
import tempfile
import unittest

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
            '[run]\nscript = "loginctl enable-linger $(id -un)"\n',
        )
        db = ApplicationsDatabase()
        assert "10-linger" in db.get_app_names()
        assert not db.app_has_sync("10-linger")
        block = db.get_blocks("10-linger")[0]
        assert blocks.block_action(block) == "run"
        assert block["when"]["os"] == ["linux"]

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


if __name__ == "__main__":
    unittest.main()
