"""Tests for block condition evaluation (mackup_ng.conditions).

Conditions live in a block's ``when`` sub-table with short keys.
"""

import os
import unittest
from unittest.mock import patch

from mackup_ng import conditions


def _b(**when):
    return {"when": when}


class TestConditions(unittest.TestCase):
    def test_empty_block_passes(self):
        assert conditions.block_passes({}) is True
        assert conditions.block_passes({"when": {}}) is True

    def test_os_any_of(self):
        with patch("mackup_ng.hooks.os_kind", return_value="linux"):
            assert conditions.block_passes(_b(os=["linux", "macos"]))
            assert not conditions.block_passes(_b(os=["macos"]))

    def test_arch(self):
        with patch("mackup_ng.conditions.platform.machine", return_value="x86_64"):
            assert conditions.block_passes(_b(arch=["x86_64"]))
            assert not conditions.block_passes(_b(arch=["aarch64"]))

    def test_markers(self):
        with patch("mackup_ng.hooks.has_marker", side_effect=lambda n: n == "eink"):
            assert conditions.block_passes(_b(marker=["eink"]))
            assert not conditions.block_passes(_b(marker=["nope"]))
            assert not conditions.block_passes(_b(not_marker=["eink"]))
            assert conditions.block_passes(_b(not_marker=["nope"]))

    def test_command(self):
        with patch(
            "mackup_ng.conditions.shutil.which",
            side_effect=lambda c: "/x" if c == "git" else None,
        ):
            assert conditions.block_passes(_b(command=["git"]))
            assert not conditions.block_passes(_b(command=["git", "nope"]))

    def test_gui(self):
        with patch("mackup_ng.hooks.has_gui", return_value=False):
            assert not conditions.block_passes(_b(gui=True))
            assert conditions.block_passes(_b(gui=False))

    def test_exists(self):
        assert conditions.block_passes(_b(exists=["/"]))
        assert not conditions.block_passes(_b(exists=["/no/such/path"]))
        assert conditions.block_passes(_b(not_exists=["/no/such/path"]))
        assert not conditions.block_passes(_b(not_exists=["/"]))

    def test_env_list_and_map(self):
        os.environ["COND_X"] = "yes"
        try:
            assert conditions.block_passes(_b(env=["COND_X"]))
            assert conditions.block_passes(_b(env={"COND_X": "yes"}))
            assert not conditions.block_passes(_b(env={"COND_X": "no"}))
            assert not conditions.block_passes(_b(env=["COND_MISSING"]))
        finally:
            os.environ.pop("COND_X", None)

    def test_multiple_conditions_all_apply(self):
        with patch("mackup_ng.hooks.os_kind", return_value="linux"), patch(
            "mackup_ng.conditions.platform.machine", return_value="x86_64",
        ):
            assert conditions.block_passes(_b(os=["linux"], arch=["x86_64"]))
            assert not conditions.block_passes(_b(os=["linux"], arch=["aarch64"]))


if __name__ == "__main__":
    unittest.main()
