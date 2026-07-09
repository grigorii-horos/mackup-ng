"""Tests for the [mapped_files] `local = backup` section."""

import os
import tempfile
import unittest

from mackup_ng.appsdb import ApplicationsDatabase


class TestMappedFiles(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="mackup_map_home_")
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

    def _write_app(self, name, body):
        with open(os.path.join(self.apps_dir, f"{name}.cfg"), "w") as handle:
            handle.write(f"[application]\nname = {name}\n\n{body}")

    def test_maps_local_to_backup(self):
        self._write_app(
            "demo",
            "[mapped_files]\n"
            ".config/app/grisa.profile/user.js = .config/app/profile/user.js\n",
        )
        mappings = ApplicationsDatabase().get_file_mappings("demo")
        assert (
            ".config/app/grisa.profile/user.js",
            ".config/app/profile/user.js",
        ) in mappings

    def test_plain_section_still_direct(self):
        self._write_app("demo", "[configuration_files]\n.plainfile\n")
        mappings = ApplicationsDatabase().get_file_mappings("demo")
        assert (".plainfile", ".plainfile") in mappings

    def test_mapping_with_braces_zips(self):
        self._write_app(
            "demo",
            "[mapped_files]\n.local/{a,b}.conf = .backup/{a,b}.conf\n",
        )
        mappings = ApplicationsDatabase().get_file_mappings("demo")
        assert (".local/a.conf", ".backup/a.conf") in mappings
        assert (".local/b.conf", ".backup/b.conf") in mappings

    def test_paths_with_spaces_and_dashes(self):
        """Spaces, dashes and arrows in paths survive (only '=' is special)."""
        self._write_app(
            "demo",
            "[mapped_files]\n"
            ".config/My App-1/a -> b.conf = .config/shared/a -> b.conf\n",
        )
        mappings = ApplicationsDatabase().get_file_mappings("demo")
        assert (
            ".config/My App-1/a -> b.conf",
            ".config/shared/a -> b.conf",
        ) in mappings

    def test_both_sections_merge(self):
        self._write_app(
            "demo",
            "[configuration_files]\n.direct.conf\n\n"
            "[mapped_files]\n.local.conf = .stored.conf\n",
        )
        mappings = ApplicationsDatabase().get_file_mappings("demo")
        assert (".direct.conf", ".direct.conf") in mappings
        assert (".local.conf", ".stored.conf") in mappings


if __name__ == "__main__":
    unittest.main()
