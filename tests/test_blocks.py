"""Tests for the unified action-block executor (mackup_ng.blocks)."""

import os
import tempfile
import unittest

from mackup_ng import blocks


class TestBlocks(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="mackup_blocks_")
        self._orig = {k: os.environ.get(k) for k in ("HOME", "XDG_STATE_HOME")}
        os.environ["HOME"] = self.home
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
            {"copy": {"from": src, "to": "~/d.txt"}}, [], dry_run=False,
        )
        assert open(os.path.join(self.home, "d.txt")).read() == "hi"

    def test_run_block_commands(self):
        state = os.path.join(self.home, "flag")
        blocks.apply_block(
            {"run": {"commands": [f'touch "{state}"']}}, [], dry_run=False,
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
            {"chmod": {
                "path": "~/.ssh",
                "recursive": True,
                "dir_mode": "700",
                "file_mode": "600",
            }},
            [],
            dry_run=False,
        )
        assert os.stat(ssh).st_mode & 0o777 == 0o700
        assert os.stat(cfg).st_mode & 0o777 == 0o600

    def test_xml_idempotent(self):
        xml_path = os.path.join(self.home, "c.xml")
        with open(xml_path, "w") as f:
            f.write("<configuration><options><a>0</a></options></configuration>")
        block = {"xml": {
            "paths": [xml_path],
            "select": ["options"],
            "set_child": {"a": "1"},
        }}
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
            {"copy": {"from": srcdir, "to": "~/.local/bin"}}, [], dry_run=False,
        )
        assert open(os.path.join(self.home, ".local/bin/tool")).read() == "bin"

    def test_apply_blocks_condition_gate(self):
        out = os.path.join(self.home, "gated")
        blocks.apply_blocks(
            [{
                "when": {"marker": ["nope"]},
                "run": {"commands": [f'touch "{out}"']},
            }],
            phase="post",
            env_files=[],
            dry_run=False,
        )
        assert not os.path.exists(out)


if __name__ == "__main__":
    unittest.main()
