"""Syncthing leaves artifacts inside synced folders; mackup must not carry them."""

import os
import shutil
import tempfile
import unittest

from mackup_ng import ignore

GLOBS = (
    "*.sync-conflict-*",
    "~syncthing~*.tmp",
    ".syncthing.*.tmp",
    ".stfolder",
    ".stversions",
    ".stignore",
)


class TestIgnoredNames(unittest.TestCase):
    def test_a_sync_conflict_copy_is_ignored(self):
        assert ignore.is_ignored(
            GLOBS,
            "notes.sync-conflict-20260824-103000-ABCDEFG.md",
        )

    def test_a_sync_conflict_copy_without_an_extension_is_ignored(self):
        assert ignore.is_ignored(GLOBS, "zshrc.sync-conflict-20260824-103000-ABCDEFG")

    def test_a_syncthing_temporary_file_is_ignored(self):
        assert ignore.is_ignored(GLOBS, "~syncthing~notes.md.tmp")

    def test_a_hidden_syncthing_temporary_file_is_ignored(self):
        assert ignore.is_ignored(GLOBS, ".syncthing.notes.md.tmp")

    def test_the_folder_marker_is_ignored(self):
        assert ignore.is_ignored(GLOBS, ".stfolder")

    def test_the_versions_folder_is_ignored(self):
        assert ignore.is_ignored(GLOBS, ".stversions")

    def test_the_ignore_list_is_ignored(self):
        assert ignore.is_ignored(GLOBS, ".stignore")

    def test_an_ordinary_file_is_kept(self):
        assert not ignore.is_ignored(GLOBS, "init.lua")

    def test_a_file_merely_mentioning_conflict_is_kept(self):
        assert not ignore.is_ignored(GLOBS, "sync-conflict-notes.md")


class TestIgnoredPaths(unittest.TestCase):
    def test_a_conflict_copy_inside_a_subdirectory_is_ignored(self):
        assert ignore.is_ignored_path(
            GLOBS,
            "lua/plugins/init.sync-conflict-20260824-103000-ABCDEFG.lua",
        )

    def test_anything_below_an_ignored_directory_is_ignored(self):
        assert ignore.is_ignored_path(GLOBS, ".stversions/notes.md")

    def test_an_ordinary_nested_path_is_kept(self):
        assert not ignore.is_ignored_path(GLOBS, "lua/plugins/init.lua")


class TestCopytreeIgnore(unittest.TestCase):
    def test_it_names_the_entries_shutil_must_skip(self):
        names = [
            "init.lua",
            "init.sync-conflict-20260824-103000-ABCDEFG.lua",
            ".stfolder",
        ]

        skipped = ignore.copytree_ignore(GLOBS)("/some/dir", names)

        assert skipped == {
            "init.sync-conflict-20260824-103000-ABCDEFG.lua",
            ".stfolder",
        }


class TestLoadGlobs(unittest.TestCase):
    """Patterns come from *.toml files, the way markers and apps do."""

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="mackup_ignore_")
        self._orig = {k: os.environ.get(k) for k in ("HOME", "XDG_CONFIG_HOME")}
        os.environ["HOME"] = self.home
        os.environ["XDG_CONFIG_HOME"] = os.path.join(self.home, ".config")
        ignore.load_globs.cache_clear()
        self.addCleanup(ignore.load_globs.cache_clear)

    def tearDown(self):
        for key, value in self._orig.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(self.home, ignore_errors=True)

    def write_ignore_file(self, directory, stem, body):
        os.makedirs(directory, exist_ok=True)
        with open(os.path.join(directory, f"{stem}.toml"), "w") as handle:
            handle.write(body)

    def test_the_built_in_syncthing_patterns_are_loaded(self):
        assert "*.sync-conflict-*" in ignore.load_globs()

    def test_a_local_file_adds_its_patterns(self):
        self.write_ignore_file(
            os.path.join(self.home, ".mackup", "ignores"),
            "mine",
            '[ignore]\nname = "Mine"\npatterns = ["*.bak"]\n',
        )

        assert "*.bak" in ignore.load_globs()

    def test_a_local_file_overrides_the_built_in_of_the_same_name(self):
        self.write_ignore_file(
            os.path.join(self.home, ".mackup", "ignores"),
            "syncthing",
            "[ignore]\npatterns = []\n",
        )

        assert "*.sync-conflict-*" not in ignore.load_globs()

    def test_the_xdg_directory_is_read_too(self):
        self.write_ignore_file(
            os.path.join(os.environ["XDG_CONFIG_HOME"], "mackup", "ignores"),
            "xdg",
            '[ignore]\npatterns = ["*.xdg-junk"]\n',
        )

        assert "*.xdg-junk" in ignore.load_globs()

    def test_a_malformed_file_is_skipped(self):
        self.write_ignore_file(
            os.path.join(self.home, ".mackup", "ignores"),
            "broken",
            "not toml {",
        )

        assert "*.sync-conflict-*" in ignore.load_globs()


if __name__ == "__main__":
    unittest.main()
