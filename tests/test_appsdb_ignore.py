"""A config can name patterns ignored inside its own paths."""

import os
import shutil
import tempfile
import unittest

from mackup_ng.appsdb import ApplicationsDatabase


class TestPerConfigIgnore(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="mackup_appignore_")
        self._orig = {k: os.environ.get(k) for k in ("HOME", "XDG_CONFIG_HOME")}
        os.environ["HOME"] = self.home
        os.environ["XDG_CONFIG_HOME"] = os.path.join(self.home, ".config")
        self.apps_dir = os.path.join(self.home, ".mackup", "applications")
        os.makedirs(self.apps_dir, exist_ok=True)

    def tearDown(self):
        for key, value in self._orig.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(self.home, ignore_errors=True)

    def _write(self, name, body):
        with open(os.path.join(self.apps_dir, f"{name}.toml"), "w") as handle:
            handle.write(f'name = "{name}"\n{body}')

    def test_the_ignore_key_is_read(self):
        self._write("noisy", 'files = [".noisy"]\nignore = ["*.bak", "*.tmp"]\n')

        assert ApplicationsDatabase().get_ignore_patterns("noisy") == ["*.bak", "*.tmp"]

    def test_a_config_without_the_key_ignores_nothing_of_its_own(self):
        self._write("quiet", 'files = [".quiet"]\n')

        assert ApplicationsDatabase().get_ignore_patterns("quiet") == []

    def test_the_ignore_key_is_not_mistaken_for_a_block(self):
        self._write("noisy", 'files = [".noisy"]\nignore = ["*.bak"]\n')

        assert ApplicationsDatabase().get_blocks("noisy") == []


if __name__ == "__main__":
    unittest.main()
