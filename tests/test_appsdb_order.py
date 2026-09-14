"""Tests for deterministic config read order and ordered getters."""

import os
import shutil
import tempfile
import unittest

from mackup_ng.appsdb import ApplicationsDatabase


class TestReadOrder(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="mackup_order_home_")
        self._orig_home = os.environ.get("HOME")
        self._orig_xdg = os.environ.get("XDG_CONFIG_HOME")
        os.environ["HOME"] = self.home
        os.environ["XDG_CONFIG_HOME"] = os.path.join(self.home, ".config")
        self.apps_dir = os.path.join(
            self.home,
            ".config",
            "mackup",
            "applications",
        )
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

    def _write(self, directory, name, body):
        with open(os.path.join(directory, f"{name}.toml"), "w") as handle:
            handle.write(f'name = "{name}"\n{body}')

    def test_custom_configs_are_read_after_stock_ones(self):
        self._write(self.apps_dir, "zzz-custom", 'files = [".customrc"]\n')
        order = ApplicationsDatabase().get_app_order()
        assert order.index("bash") < order.index("zzz-custom")

    def test_stock_configs_are_ordered_alphabetically(self):
        order = ApplicationsDatabase().get_app_order()
        stock = [name for name in order if name in {"bash", "git", "vim"}]
        assert stock == sorted(stock)

    def test_entries_keep_declaration_order(self):
        self._write(
            self.apps_dir,
            "ordered",
            'files = [".zshrc", ".bashrc"]\n\n'
            "[mapped_files]\n"
            '".config/b" = ".config/a"\n',
        )
        db = ApplicationsDatabase()
        assert db.get_file_mappings("ordered") == [
            (".zshrc", ".zshrc"),
            (".bashrc", ".bashrc"),
            (".config/b", ".config/a"),
        ]
        assert db.get_files("ordered") == [".zshrc", ".bashrc", ".config/b"]

    def test_same_named_custom_config_replaces_the_stock_one(self):
        self._write(self.apps_dir, "bash", 'files = [".only-this"]\n')
        db = ApplicationsDatabase()
        assert db.get_files("bash") == [".only-this"]
