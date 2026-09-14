"""`mackup apply` runs actions and never syncs files."""

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


class TestApplyUnits(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="mackup_apply_home_")
        self.storage = tempfile.mkdtemp(prefix="mackup_apply_store_")
        self._orig = {
            key: os.environ.get(key)
            for key in ("HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME")
        }
        os.environ["HOME"] = self.home
        os.environ["XDG_CONFIG_HOME"] = os.path.join(self.home, ".config")
        os.environ["XDG_DATA_HOME"] = os.path.join(self.home, ".local", "share")
        os.environ["XDG_STATE_HOME"] = os.path.join(self.home, ".local", "state")
        os.makedirs(os.path.join(self.storage, "Mackup"), exist_ok=True)
        write_config(
            os.path.join(self.home, ".config", "mackup", "config.toml"),
            storage_path=self.storage,
            sync=["mixed"],
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

    def test_apply_runs_the_action_and_leaves_the_files_alone(self):
        backup = os.path.join(self.storage, "Mackup", ".applyfile")
        with open(backup, "w") as handle:
            handle.write("from backup\n")
        marker = os.path.join(self.home, "action-ran")

        with open(os.path.join(self.apps_dir, "mixed.toml"), "w") as handle:
            handle.write(
                'name = "Mixed"\n'
                "\n"
                "[[block]]\n"
                'files = [".applyfile"]\n'
                "[block.run]\n"
                f'script = \'touch "{marker}"\'\n',
            )

        with patch("sys.argv", ["mackup", "apply"]):
            main()

        assert os.path.exists(marker), "apply did not run the action"
        assert not os.path.exists(os.path.join(self.home, ".applyfile")), (
            "apply synced files, which is exactly what it must not do"
        )

    def test_apply_runs_units_in_slot_order_not_phase_buckets(self):
        """A phase-less block defaults to "during"; the old `apply` branch
        instead ran three bucket passes over the config's already
        slot-ordered blocks -- "pre", then "during" (blocks whose OWN
        ``phase`` key is literally "during"), then "post" (blocks whose own
        key is "post" OR missing, since `apply_blocks` defaults a missing
        key to "post" for its own filtering).

        A block with no `phase` key therefore never matched the "during"
        pass; it was deferred to the "post" pass. So when the config
        declares a phase-less block *before* an explicit `phase = "during"`
        block, the old code ran the explicit "during" block first (in the
        "during" pass) and the phase-less block second (in the "post"
        pass) -- the reverse of their order in the config.

        The new code just walks `get_units()` in slot order, which keeps
        the config's own relative order for two blocks landing in the same
        (here: "during") bucket. So this pair discriminates: old runs
        "during" then "phaseless"; new runs "phaseless" then "during".
        """
        order_log = os.path.join(self.home, "order.log")

        with open(os.path.join(self.apps_dir, "mixed.toml"), "w") as handle:
            handle.write(
                'name = "Mixed"\n'
                "\n"
                "[[block]]\n"
                "[block.run]\n"
                f'script = \'echo phaseless >> "{order_log}"\'\n'
                "\n"
                "[[block]]\n"
                'phase = "during"\n'
                "[block.run]\n"
                f'script = \'echo during >> "{order_log}"\'\n',
            )

        with patch("sys.argv", ["mackup", "apply"]):
            main()

        with open(order_log) as handle:
            recorded = [line.strip() for line in handle]

        assert recorded == ["phaseless", "during"], (
            "expected the config's own order (phaseless before the explicit "
            f"during block), got {recorded}"
        )
