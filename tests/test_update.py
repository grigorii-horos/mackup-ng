"""Tests for the PyPI update check. Nothing here touches the network."""

import json
import os
import shutil
import tempfile
import unittest

from mackup_ng import update


class TestParseVersion(unittest.TestCase):
    def test_numeric_versions_parse_to_tuples(self):
        assert update.parse_version("2.1.0") == (2, 1, 0)
        assert update.parse_version("10") == (10,)
        assert update.parse_version(" 2.1.0 ") == (2, 1, 0)

    def test_pre_releases_and_junk_are_rejected(self):
        for text in ("2.2.0rc1", "2.2.0.dev3", "2.2.0-1", "", "unknown", "v2.1.0"):
            assert update.parse_version(text) is None


class TestIsNewer(unittest.TestCase):
    def test_newer_version_wins(self):
        assert update.is_newer("2.2.0", "2.1.0")
        assert update.is_newer("2.1.1", "2.1.0")
        assert update.is_newer("2.1.0", "2.1")

    def test_equal_or_older_is_not_newer(self):
        assert not update.is_newer("2.1.0", "2.1.0")
        assert not update.is_newer("2.0.9", "2.1.0")

    def test_unparseable_sides_are_never_newer(self):
        assert not update.is_newer("2.2.0rc1", "2.1.0")
        assert not update.is_newer("2.2.0", "unknown")


class TestUpgradeCommand(unittest.TestCase):
    def test_snap_path(self):
        assert (
            update.upgrade_command("/snap/mackup-ng/12/bin/mackup-ng")
            == "sudo snap refresh mackup-ng"
        )

    def test_uv_tool_path(self):
        assert (
            update.upgrade_command(
                "/home/x/.local/share/uv/tools/mackup-ng/bin/mackup-ng",
            )
            == "uv tool upgrade mackup-ng"
        )

    def test_pipx_path(self):
        assert (
            update.upgrade_command("/home/x/.local/pipx/venvs/mackup-ng/bin/mackup-ng")
            == "pipx upgrade mackup-ng"
        )

    def test_anything_else_falls_back_to_pip(self):
        assert (
            update.upgrade_command("/usr/local/bin/mackup-ng")
            == "pip install --upgrade mackup-ng"
        )

    def test_missing_path_gives_no_command(self):
        assert update.upgrade_command("") is None


class TestCache(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="mackup_update_home_")
        self._orig = {key: os.environ.get(key) for key in ("HOME", "XDG_CACHE_HOME")}
        os.environ["HOME"] = self.home
        os.environ["XDG_CACHE_HOME"] = os.path.join(self.home, ".cache")

    def tearDown(self):
        for key, orig in self._orig.items():
            if orig is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = orig
        shutil.rmtree(self.home, ignore_errors=True)

    def test_cache_path_lives_under_xdg_cache_home(self):
        assert update.cache_path() == os.path.join(
            os.environ["XDG_CACHE_HOME"],
            "mackup",
            "update-check.json",
        )

    def test_cache_path_ignores_a_relative_xdg_cache_home(self):
        """cache_path() must go through dirs.py's non-empty-and-absolute rule.

        The inline `os.environ.get(VAR) or default` this used to use accepted
        a relative value, unlike dirs._base.
        """
        os.environ["XDG_CACHE_HOME"] = "relative/cache"
        assert update.cache_path() == os.path.join(
            self.home,
            ".cache",
            "mackup",
            "update-check.json",
        )

    def test_absent_cache_reads_as_none(self):
        assert update.read_cache(1000.0) is None

    def test_written_value_reads_back_within_the_ttl(self):
        update.write_cache("2.2.0", 1000.0)
        assert update.read_cache(1000.0) == "2.2.0"
        assert update.read_cache(1000.0 + update.CACHE_TTL_SECONDS - 1) == "2.2.0"

    def test_expired_entry_reads_as_none(self):
        update.write_cache("2.2.0", 1000.0)
        assert update.read_cache(1000.0 + update.CACHE_TTL_SECONDS + 1) is None

    def test_malformed_cache_reads_as_none(self):
        path = update.cache_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as handle:
            handle.write("{not json")
        assert update.read_cache(1000.0) is None

    def test_cache_missing_keys_reads_as_none(self):
        path = update.cache_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as handle:
            json.dump({"latest": "2.2.0"}, handle)
        assert update.read_cache(1000.0) is None

    def test_unwritable_cache_directory_is_swallowed(self):
        os.makedirs(os.environ["XDG_CACHE_HOME"], exist_ok=True)
        blocker = os.path.join(os.environ["XDG_CACHE_HOME"], "mackup")
        with open(blocker, "w") as handle:  # a file where the directory belongs
            handle.write("in the way\n")
        update.write_cache("2.2.0", 1000.0)  # must not raise
        assert update.read_cache(1000.0) is None

    def test_xdg_set_home_unset_cache_path_works(self):
        # With XDG_CACHE_HOME set and HOME removed from the environment:
        # cache_path() returns a path under XDG_CACHE_HOME,
        # and write_cache/read_cache work normally.
        os.environ.pop("HOME", None)
        update.write_cache("2.2.0", 1000.0)
        assert update.read_cache(1000.0) == "2.2.0"

    def test_both_xdg_and_home_unset_write_cache_does_not_raise(self):
        # With BOTH XDG_CACHE_HOME and HOME removed:
        # write_cache("2.2.0", 1000.0) does not raise,
        # and read_cache(1000.0) returns None.
        os.environ.pop("HOME", None)
        os.environ.pop("XDG_CACHE_HOME", None)
        update.write_cache("2.2.0", 1000.0)  # must not raise
        assert update.read_cache(1000.0) is None


class TestCheck(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="mackup_check_home_")
        self._orig = {
            key: os.environ.get(key)
            for key in ("HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME")
        }
        os.environ["HOME"] = self.home
        os.environ["XDG_CACHE_HOME"] = os.path.join(self.home, ".cache")
        os.environ["XDG_STATE_HOME"] = os.path.join(self.home, ".local", "state")
        self.calls = 0

    def tearDown(self):
        for key, orig in self._orig.items():
            if orig is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = orig
        shutil.rmtree(self.home, ignore_errors=True)

    def _fetch(self, value):
        def fetch():
            self.calls += 1
            return value

        return fetch

    def _set_marker(self, name):
        markers = os.path.join(os.environ["XDG_STATE_HOME"], "mackup", "markers")
        os.makedirs(markers, exist_ok=True)
        open(os.path.join(markers, name), "a").close()

    def test_newer_version_produces_a_line(self):
        line = update.check("2.1.0", fetch=self._fetch("2.2.0"), now=1000.0)
        assert line is not None
        assert "2.1.0" in line
        assert "2.2.0" in line

    def test_same_version_says_nothing(self):
        assert update.check("2.1.0", fetch=self._fetch("2.1.0"), now=1000.0) is None

    def test_older_remote_version_says_nothing(self):
        assert update.check("2.1.0", fetch=self._fetch("2.0.0"), now=1000.0) is None

    def test_pre_release_is_ignored(self):
        assert update.check("2.1.0", fetch=self._fetch("2.2.0rc1"), now=1000.0) is None

    def test_failed_fetch_says_nothing(self):
        assert update.check("2.1.0", fetch=self._fetch(None), now=1000.0) is None

    def test_raising_fetch_says_nothing(self):
        def boom():
            raise OSError("network down")

        assert update.check("2.1.0", fetch=boom, now=1000.0) is None

    def test_fresh_cache_suppresses_the_fetch(self):
        update.write_cache("2.2.0", 1000.0)
        line = update.check("2.1.0", fetch=self._fetch("9.9.9"), now=1000.0)
        assert self.calls == 0
        assert line is not None
        assert "2.2.0" in line

    def test_expired_cache_triggers_a_fetch_and_is_rewritten(self):
        update.write_cache("2.1.5", 1000.0)
        later = 1000.0 + update.CACHE_TTL_SECONDS + 1
        line = update.check("2.1.0", fetch=self._fetch("2.3.0"), now=later)
        assert self.calls == 1
        assert line is not None
        assert "2.3.0" in line
        assert update.read_cache(later) == "2.3.0"

    def test_marker_suppresses_everything(self):
        self._set_marker("no-update-check")
        update.write_cache("2.2.0", 1000.0)
        assert update.check("2.1.0", fetch=self._fetch("2.2.0"), now=1000.0) is None
        assert self.calls == 0

    def test_line_carries_the_upgrade_command(self):
        line = update.check("2.1.0", fetch=self._fetch("2.2.0"), now=1000.0)
        assert line is not None
        assert "Upgrade:" in line
