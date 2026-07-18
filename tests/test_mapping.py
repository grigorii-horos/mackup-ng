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
        with open(os.path.join(self.apps_dir, f"{name}.toml"), "w") as handle:
            handle.write(f'name = "{name}"\n{body}')

    def test_maps_local_to_backup(self):
        self._write_app(
            "demo",
            "\n[mapped_files]\n"
            '".config/app/grisa.profile/user.js" = ".config/app/profile/user.js"\n',
        )
        mappings = ApplicationsDatabase().get_file_mappings("demo")
        assert (
            ".config/app/grisa.profile/user.js",
            ".config/app/profile/user.js",
        ) in mappings

    def test_plain_section_still_direct(self):
        self._write_app("demo", 'files = [".plainfile"]\n')
        mappings = ApplicationsDatabase().get_file_mappings("demo")
        assert (".plainfile", ".plainfile") in mappings

    def test_mapping_with_braces_zips(self):
        self._write_app(
            "demo",
            '\n[mapped_files]\n".local/{a,b}.conf" = ".backup/{a,b}.conf"\n',
        )
        mappings = ApplicationsDatabase().get_file_mappings("demo")
        assert (".local/a.conf", ".backup/a.conf") in mappings
        assert (".local/b.conf", ".backup/b.conf") in mappings

    def test_paths_with_spaces_and_dashes(self):
        """Spaces, dashes and arrows in paths survive (only '=' is special)."""
        self._write_app(
            "demo",
            "\n[mapped_files]\n"
            '".config/My App-1/a -> b.conf" = ".config/shared/a -> b.conf"\n',
        )
        mappings = ApplicationsDatabase().get_file_mappings("demo")
        assert (
            ".config/My App-1/a -> b.conf",
            ".config/shared/a -> b.conf",
        ) in mappings

    def test_env_var_from_environment(self):
        """A non-reserved ${VAR} resolves from the environment."""
        os.environ["FF_PROFILE"] = "abc.default"
        try:
            self._write_app(
                "demo",
                'files = ["${MACKUP_XDG_CONFIG}/ff/${FF_PROFILE}/prefs.js"]\n',
            )
            files = ApplicationsDatabase().get_files("demo")
            assert ".config/ff/abc.default/prefs.js" in files
        finally:
            os.environ.pop("FF_PROFILE", None)

    def test_env_var_from_source_env_file(self):
        """A ${VAR} resolves from a source_env file when not in the environment."""
        env_file = os.path.join(self.home, "mackup-env")
        with open(env_file, "w") as handle:
            handle.write('# comment\nFF_PROFILE = "xyz.default"\n')
        os.environ.pop("FF_PROFILE", None)
        self._write_app(
            "demo",
            f'source_env = ["{env_file}"]\n'
            'files = ["${MACKUP_XDG_CONFIG}/ff/${FF_PROFILE}/prefs.js"]\n',
        )
        files = ApplicationsDatabase().get_files("demo")
        assert ".config/ff/xyz.default/prefs.js" in files

    def test_unresolved_env_var_skips_entry(self):
        """An unresolved ${VAR} skips just that entry, not the whole app."""
        os.environ.pop("NOPE", None)
        self._write_app(
            "demo",
            'files = ["${MACKUP_XDG_CONFIG}/a", "${NOPE}/b"]\n',
        )
        files = ApplicationsDatabase().get_files("demo")
        assert ".config/a" in files
        assert not any("NOPE" in f or f.endswith("/b") for f in files)

    def test_both_sections_merge(self):
        self._write_app(
            "demo",
            'files = [".direct.conf"]\n\n'
            '[mapped_files]\n".local.conf" = ".stored.conf"\n',
        )
        mappings = ApplicationsDatabase().get_file_mappings("demo")
        assert (".direct.conf", ".direct.conf") in mappings
        assert (".local.conf", ".stored.conf") in mappings


if __name__ == "__main__":
    unittest.main()
