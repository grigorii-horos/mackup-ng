"""A config's units execute in slot order, files before action."""

import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from mackup_ng import utils
from mackup_ng.main import main

from .conftest import write_config

# main() reads sys.argv and takes no arguments; tests/test_cli.py drives it
# with patch("sys.argv", [...]). Follow that, not main(["sync"]).


class TestUnitOrder(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="mackup_order_home_")
        self.storage = tempfile.mkdtemp(prefix="mackup_order_store_")
        self._orig = {
            key: os.environ.get(key)
            for key in ("HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME")
        }
        os.environ["HOME"] = self.home
        os.environ["XDG_CONFIG_HOME"] = os.path.join(self.home, ".config")
        os.environ["XDG_DATA_HOME"] = os.path.join(self.home, ".local", "share")
        os.environ["XDG_STATE_HOME"] = os.path.join(self.home, ".local", "state")
        os.makedirs(os.path.join(self.storage, "Mackup"), exist_ok=True)
        self.config_path = os.path.join(
            self.home, ".config", "mackup", "config.toml",
        )
        write_config(
            self.config_path,
            storage_path=self.storage,
            sync=["ordered"],
        )
        self.apps_dir = os.path.join(self.home, ".config", "mackup", "applications")
        os.makedirs(self.apps_dir, exist_ok=True)
        utils.FORCE_YES = True
        self.fetch_patcher = patch("mackup_ng.update.fetch_latest", return_value=None)
        self.fetch_patcher.start()
        self.addCleanup(self.fetch_patcher.stop)

    def tearDown(self):
        for key, value in self._orig.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(self.home, ignore_errors=True)
        shutil.rmtree(self.storage, ignore_errors=True)
        utils.FORCE_YES = False

    def _write_app(self, body):
        with open(os.path.join(self.apps_dir, "ordered.toml"), "w") as handle:
            handle.write(body)

    def test_a_unit_action_sees_the_files_that_unit_just_synced(self):
        """Files first, then the action — within one unit."""
        backup = os.path.join(self.storage, "Mackup", ".unitfile")
        with open(backup, "w") as handle:
            handle.write("from backup\n")
        marker = os.path.join(self.home, "saw-it")

        self._write_app(
            'name = "Ordered"\n'
            "\n"
            "[[block]]\n"
            'files = [".unitfile"]\n'
            "[block.run]\n"
            f'script = \'test -f "$HOME/.unitfile" && touch "{marker}"\'\n',
        )

        with patch("sys.argv", ["mackup", "sync"]):
            main()

        assert os.path.exists(os.path.join(self.home, ".unitfile"))
        assert os.path.exists(marker), "the action ran before its unit's files"

    def test_a_gated_unit_syncs_nothing_and_does_not_run(self):
        backup = os.path.join(self.storage, "Mackup", ".gatedfile")
        with open(backup, "w") as handle:
            handle.write("from backup\n")
        marker = os.path.join(self.home, "should-not-exist")

        self._write_app(
            'name = "Ordered"\n'
            "\n"
            "[[block]]\n"
            'files = [".gatedfile"]\n'
            "[block.when]\n"
            'os = "definitely-not-this-os"\n'
            "[block.run]\n"
            f'script = \'touch "{marker}"\'\n',
        )

        with patch("sys.argv", ["mackup", "sync"]):
            main()

        assert not os.path.exists(os.path.join(self.home, ".gatedfile"))
        assert not os.path.exists(marker)

    def test_a_pre_block_runs_before_the_top_level_files(self):
        backup = os.path.join(self.storage, "Mackup", ".topfile")
        with open(backup, "w") as handle:
            handle.write("from backup\n")
        marker = os.path.join(self.home, "pre-ran-first")

        self._write_app(
            'name = "Ordered"\n'
            'files = [".topfile"]\n'
            "\n"
            "[[block]]\n"
            'phase = "pre"\n'
            "[block.run]\n"
            f'script = \'test ! -f "$HOME/.topfile" && touch "{marker}"\'\n',
        )

        with patch("sys.argv", ["mackup", "sync"]):
            main()

        assert os.path.exists(os.path.join(self.home, ".topfile"))
        assert os.path.exists(marker), "the pre block ran after the top-level files"

    def test_a_later_units_file_is_not_yet_synced_when_an_earlier_unit_acts(self):
        """Two units, same (default) phase — each unit still completes in full
        (files, then its action) before the next unit's files sync, rather
        than every unit's files syncing first and every action running after.
        """
        for name in (".block1file", ".block2file"):
            with open(os.path.join(self.storage, "Mackup", name), "w") as handle:
                handle.write("from backup\n")
        marker = os.path.join(self.home, "block2-not-yet-there")

        self._write_app(
            'name = "Ordered"\n'
            "\n"
            "[[block]]\n"
            'files = [".block1file"]\n'
            "[block.run]\n"
            f'script = \'test ! -f "$HOME/.block2file" && touch "{marker}"\'\n'
            "\n"
            "[[block]]\n"
            'files = [".block2file"]\n',
        )

        with patch("sys.argv", ["mackup", "sync"]):
            main()

        assert os.path.exists(os.path.join(self.home, ".block1file"))
        assert os.path.exists(os.path.join(self.home, ".block2file"))
        assert os.path.exists(marker), (
            "block 2's file was already synced when block 1's action ran"
        )
