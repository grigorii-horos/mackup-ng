"""Tests for the unified action-block executor (mackup_ng.blocks)."""

import os
import tempfile
import unittest
from collections import Counter
from unittest import mock

from mackup_ng import blocks, dirs


class TestBlocks(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="mackup_blocks_")
        self._orig = {
            k: os.environ.get(k)
            for k in ("HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME")
        }
        os.environ["HOME"] = self.home
        os.environ["XDG_CONFIG_HOME"] = os.path.join(self.home, ".config")
        os.environ["XDG_DATA_HOME"] = os.path.join(self.home, ".local", "share")
        os.environ["XDG_STATE_HOME"] = os.path.join(self.home, ".local", "state")

    def tearDown(self):
        for k, v in self._orig.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_copy_block_applies(self):
        src = os.path.join(self.home, "s.txt")
        with open(src, "w") as f:
            f.write("hi")
        blocks.apply_block(
            {"copy": {"from": src, "to": "~/d.txt"}},
            [],
            dry_run=False,
        )
        assert open(os.path.join(self.home, "d.txt")).read() == "hi"

    def test_run_block_commands(self):
        state = os.path.join(self.home, "flag")
        blocks.apply_block(
            {"run": {"commands": [f'touch "{state}"']}},
            [],
            dry_run=False,
        )
        assert os.path.isfile(state)

    def test_no_action_skipped(self):
        # no action sub-table -> must not raise
        blocks.apply_block({"bogus": {}}, [], dry_run=False)
        blocks.apply_block({}, [], dry_run=False)

    def test_apply_blocks_phase_and_order(self):
        a = os.path.join(self.home, "a")
        b = os.path.join(self.home, "b")
        blocks.apply_blocks(
            [
                {"phase": "pre", "run": {"commands": [f'echo x > "{a}"']}},
                {"phase": "post", "run": {"commands": [f'echo x > "{b}"']}},
            ],
            phase="pre",
            env_files=[],
            dry_run=False,
        )
        assert os.path.isfile(a)
        assert not os.path.exists(b)

    def test_chmod_recursive_dir_file_modes(self):
        ssh = os.path.join(self.home, ".ssh")
        os.makedirs(ssh)
        cfg = os.path.join(ssh, "config")
        with open(cfg, "w") as f:
            f.write("x")
        os.chmod(ssh, 0o755)
        os.chmod(cfg, 0o644)
        blocks.apply_block(
            {
                "chmod": {
                    "path": "~/.ssh",
                    "recursive": True,
                    "dir_mode": "700",
                    "file_mode": "600",
                },
            },
            [],
            dry_run=False,
        )
        assert os.stat(ssh).st_mode & 0o777 == 0o700
        assert os.stat(cfg).st_mode & 0o777 == 0o600

    def test_xml_idempotent(self):
        xml_path = os.path.join(self.home, "c.xml")
        with open(xml_path, "w") as f:
            f.write("<configuration><options><a>0</a></options></configuration>")
        block = {
            "xml": {
                "paths": [xml_path],
                "select": ["options"],
                "set_child": {"a": "1"},
            },
        }
        blocks.apply_block(block, [], dry_run=False)
        first = open(xml_path, "rb").read()
        assert b"<a>1</a>" in first
        blocks.apply_block(block, [], dry_run=False)
        assert open(xml_path, "rb").read() == first

    def test_copy_directory_merges(self):
        srcdir = os.path.join(self.home, "apps")
        os.makedirs(srcdir)
        with open(os.path.join(srcdir, "tool"), "w") as f:
            f.write("bin")
        blocks.apply_block(
            {"copy": {"from": srcdir, "to": "~/.local/bin"}},
            [],
            dry_run=False,
        )
        assert open(os.path.join(self.home, ".local/bin/tool")).read() == "bin"

    def test_apply_blocks_condition_gate(self):
        out = os.path.join(self.home, "gated")
        blocks.apply_blocks(
            [
                {
                    "when": {"marker": ["nope"]},
                    "run": {"commands": [f'touch "{out}"']},
                },
            ],
            phase="post",
            env_files=[],
            dry_run=False,
        )
        assert not os.path.exists(out)

    def test_restart_service_coalesced_across_blocks(self):
        """Many blocks touching one service must yield a single stop/start pair.

        Bouncing the unit per block trips systemd's StartLimitBurst and leaves
        the service dead (Result: start-limit-hit).
        """
        state = {"running": True}
        stops: list[str] = []
        starts: list[str] = []

        def fake_is_active(svc):
            return bool(svc) and state["running"]

        def fake_stop(svc):
            state["running"] = False
            stops.append(svc)

        def fake_start(svc):
            state["running"] = True
            starts.append(svc)

        run_blocks = [
            {"restart_service": "syncthing", "run": {"commands": ["true"]}}
            for _ in range(6)
        ]
        with mock.patch.object(blocks, "svc_is_active", fake_is_active), \
             mock.patch.object(blocks, "svc_stop", fake_stop), \
             mock.patch.object(blocks, "svc_start", fake_start):
            blocks.apply_blocks(run_blocks, phase="post", env_files=[], dry_run=False)

        assert stops == ["syncthing"]
        assert starts == ["syncthing"]
        assert state["running"] is True

    def test_dropin_path_uses_dirs_module(self):
        """dropin_path resolves $XDG_CONFIG_HOME through dirs.py, not inline."""
        path = blocks.dropin_path({"service": "syncthing", "name": "limits"})
        assert path == os.path.join(
            dirs.user_config_home(),
            "systemd",
            "user",
            "syncthing.service.d",
            "limits.conf",
        )

    def test_dropin_path_ignores_relative_xdg_config_home(self):
        """A relative $XDG_CONFIG_HOME must fall back, like every other base."""
        os.environ["XDG_CONFIG_HOME"] = "relative/cfg"
        path = blocks.dropin_path({"service": "syncthing"})
        assert path == os.path.join(
            self.home,
            ".config",
            "systemd",
            "user",
            "syncthing.service.d",
            "mackup-set.conf",
        )


def test_three_blocks_of_one_config_restart_a_service_once(monkeypatch):
    """The deferral must span the whole config, not one phase: three blocks
    that each declare restart_service = "x" are one stop and one start."""
    calls = []
    state = {"running": True}

    def fake_is_active(svc):
        return bool(svc) and state["running"]

    def fake_stop(svc):
        state["running"] = False
        calls.append(("stop", svc))

    def fake_start(svc):
        state["running"] = True
        calls.append(("start", svc))

    monkeypatch.setattr(blocks, "svc_is_active", fake_is_active)
    monkeypatch.setattr(blocks, "svc_stop", fake_stop)
    monkeypatch.setattr(blocks, "svc_start", fake_start)

    cfg_blocks = [
        {"restart_service": "x", "run": {"script": "true"}},
        {"restart_service": "x", "run": {"script": "true"}},
        {"restart_service": "x", "run": {"script": "true"}},
    ]
    pending: set[str] = set()
    for block in cfg_blocks:
        blocks.apply_unit_action(block, [], dry_run=False, pending_starts=pending)
    blocks.flush_pending_starts(pending)

    assert calls.count(("stop", "x")) == 1
    assert calls.count(("start", "x")) == 1
    assert calls[-1] == ("start", "x")


def test_apply_unit_action_honours_the_block_condition(tmp_path, monkeypatch):
    monkeypatch.setattr(blocks, "svc_stop", lambda svc: None)
    monkeypatch.setattr(blocks, "svc_start", lambda svc: None)
    marker = tmp_path / "should-not-exist"

    block = {
        "when": {"os": "definitely-not-this-os"},
        "run": {"script": f'touch "{marker}"'},
    }
    pending: set[str] = set()
    tally = blocks.apply_unit_action(block, [], dry_run=False, pending_starts=pending)

    assert tally == Counter()
    assert not marker.exists(), "the action ran despite its condition failing"


if __name__ == "__main__":
    unittest.main()
