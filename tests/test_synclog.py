import json
import os
import shutil
import tempfile
import unittest

from mackup_ng import synclog


class TestSyncLog(unittest.TestCase):
    """Machine-local record of what the last sync did to each destination."""

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="mackup_synclog_")
        self._orig = {k: os.environ.get(k) for k in ("HOME", "XDG_STATE_HOME")}
        os.environ["HOME"] = self.home
        os.environ["XDG_STATE_HOME"] = os.path.join(self.home, ".local", "state")

    def tearDown(self):
        for key, value in self._orig.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(self.home, ignore_errors=True)

    def test_log_path_lives_under_xdg_state_home(self):
        assert synclog.log_path() == os.path.join(
            os.environ["XDG_STATE_HOME"],
            "mackup",
            "sync-log.json",
        )

    def test_read_returns_nothing_without_a_log(self):
        assert synclog.read() == {}

    def test_record_then_read_returns_the_entry(self):
        synclog.record(
            {".zshrc": {"ts": 42.0, "action": "Backed up", "source": ".zshrc"}},
        )

        assert synclog.read() == {
            ".zshrc": {"ts": 42.0, "action": "Backed up", "source": ".zshrc"},
        }

    def test_record_merges_into_the_existing_log(self):
        synclog.record(
            {".zshrc": {"ts": 1.0, "action": "Backed up", "source": ".zshrc"}},
        )
        synclog.record(
            {".vimrc": {"ts": 2.0, "action": "Restored", "source": ".vimrc"}},
        )

        assert sorted(synclog.read()) == [".vimrc", ".zshrc"]

    def test_record_overwrites_the_previous_entry_for_a_destination(self):
        synclog.record(
            {".zshrc": {"ts": 1.0, "action": "Backed up", "source": ".zshrc"}},
        )
        synclog.record(
            {".zshrc": {"ts": 9.0, "action": "Restored", "source": ".zshrc"}},
        )

        assert synclog.read()[".zshrc"]["action"] == "Restored"

    def test_read_ignores_a_corrupt_log(self):
        path = synclog.log_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as handle:
            handle.write("{not json")

        assert synclog.read() == {}

    def test_record_normalizes_the_destination_path(self):
        synclog.record(
            {"./.zshrc": {"ts": 1.0, "action": "Backed up", "source": ".zshrc"}},
        )

        assert ".zshrc" in synclog.read()

    def test_lookup_finds_an_entry_by_any_spelling_of_the_path(self):
        synclog.record(
            {".zshrc": {"ts": 7.0, "action": "Backed up", "source": ".zshrc"}},
        )

        entry = synclog.lookup(synclog.read(), os.path.join(self.home, ".zshrc"))

        assert entry is not None
        assert entry["ts"] == 7.0

    def test_a_log_that_is_not_a_mapping_reads_as_empty(self):
        path = synclog.log_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as handle:
            json.dump([1, 2], handle)

        assert synclog.read() == {}
