"""A config is an ordered sequence of units, numbered in execution order."""

import io
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from mackup_ng.appsdb import ApplicationsDatabase


class TestUnits(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="mackup_units_home_")
        self._orig = {
            key: os.environ.get(key)
            for key in ("HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME")
        }
        os.environ["HOME"] = self.home
        os.environ["XDG_CONFIG_HOME"] = os.path.join(self.home, ".config")
        os.environ["XDG_DATA_HOME"] = os.path.join(self.home, ".local", "share")
        os.environ["XDG_STATE_HOME"] = os.path.join(self.home, ".local", "state")
        self.apps_dir = os.path.join(self.home, ".config", "mackup", "applications")
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
            handle.write(body)

    def test_slots_are_numbered_in_execution_order(self):
        """pre block, then the top-level unit, then during, then post."""
        self._write(
            "ordered",
            'name = "Ordered"\n'
            'files = [".toplevel"]\n'
            "\n"
            "[[block]]\n"
            'phase = "post"\n'
            'files = [".late"]\n'
            "\n"
            "[[block]]\n"
            'phase = "pre"\n'
            'files = [".early"]\n'
            "\n"
            "[[block]]\n"
            'files = [".middle"]\n',
        )
        units = ApplicationsDatabase().get_units("ordered")

        locals_by_slot = [
            [local for local, _backup in unit.mappings] for unit in units
        ]
        assert [unit.slot for unit in units] == [0, 1, 2, 3]
        assert locals_by_slot == [
            [".early"],
            [".toplevel"],
            [".middle"],
            [".late"],
        ]

    def test_a_block_defaults_to_during(self):
        self._write(
            "defaulted",
            'name = "Defaulted"\nfiles = [".top"]\n\n[[block]]\nfiles = [".after"]\n',
        )
        units = ApplicationsDatabase().get_units("defaulted")

        assert [[local for local, _ in u.mappings] for u in units] == [
            [".top"],
            [".after"],
        ]

    def test_a_failing_unit_contributes_no_mappings(self):
        self._write(
            "gated",
            'name = "Gated"\n'
            'files = [".always"]\n'
            "\n"
            "[[block]]\n"
            'files = [".mac-only"]\n'
            "[block.when]\n"
            'os = "definitely-not-this-os"\n',
        )
        db = ApplicationsDatabase()

        units = db.get_units("gated")
        assert [unit.passed for unit in units] == [True, False]
        assert db.get_file_mappings("gated") == [(".always", ".always", 0)]

    def test_file_mappings_carry_their_slot(self):
        self._write(
            "slotted",
            'name = "Slotted"\nfiles = [".a"]\n\n[[block]]\nfiles = [".b"]\n',
        )

        assert ApplicationsDatabase().get_file_mappings("slotted") == [
            (".a", ".a", 0),
            (".b", ".b", 1),
        ]

    def test_a_block_may_carry_both_files_and_an_action(self):
        self._write(
            "both",
            'name = "Both"\n'
            "\n"
            "[[block]]\n"
            'files = [".thing"]\n'
            "[block.chmod]\n"
            'path = "~/.thing"\n'
            'file_mode = "600"\n',
        )
        units = ApplicationsDatabase().get_units("both")

        unit = units[-1]
        assert [local for local, _ in unit.mappings] == [".thing"]
        assert unit.block is not None
        assert "chmod" in unit.block

    def test_get_units_returns_actions_of_passing_units_in_slot_order(self):
        self._write(
            "acts",
            'name = "Acts"\n'
            "\n"
            "[[block]]\n"
            'phase = "post"\n'
            "[block.run]\n"
            'script = "echo late"\n'
            "\n"
            "[[block]]\n"
            'phase = "pre"\n'
            "[block.run]\n"
            'script = "echo early"\n',
        )
        scripts = [
            unit.block["run"]["script"]
            for unit in ApplicationsDatabase().get_units("acts")
            if unit.passed and unit.block is not None
        ]

        assert scripts == ["echo early", "echo late"]

    def test_unknown_phase_warns_and_is_treated_as_during(self):
        self._write(
            "badphase",
            'name = "Bad"\nfiles = [".top"]\n\n[[block]]\n'
            'phase = "whenever"\nfiles = [".other"]\n',
        )
        units = ApplicationsDatabase().get_units("badphase")

        assert [[local for local, _ in u.mappings] for u in units] == [
            [".top"],
            [".other"],
        ]

    def test_unknown_phase_warns_exactly_once_per_block(self):
        """`_phase_of` used to be called from three list comprehensions, so one
        bad phase warned three times per block build. It must warn once.
        """
        self._write(
            "badphase_once",
            'name = "Bad"\nfiles = [".top"]\n\n[[block]]\n'
            'phase = "whenever"\nfiles = [".other"]\n',
        )
        buffer = io.StringIO()
        with patch("sys.stdout", buffer):
            ApplicationsDatabase()

        assert buffer.getvalue().count("unknown phase") == 1

    def test_a_passing_files_only_block_carries_no_action(self):
        """A [[block]] with only `files` must not reach the action executor:
        `Unit.block` is None unless the unit actually carries an action.
        """
        self._write(
            "files_only",
            'name = "FilesOnly"\nfiles = [".top"]\n\n[[block]]\nfiles = [".other"]\n',
        )
        units = ApplicationsDatabase().get_units("files_only")

        files_only_unit = next(u for u in units if u.slot == 1)
        assert files_only_unit.passed is True
        assert files_only_unit.block is None

    def test_a_block_with_neither_files_nor_action_warns_and_is_skipped(self):
        self._write(
            "neither",
            'name = "Neither"\nfiles = [".top"]\n\n[[block]]\n[block.when]\nos = "linux"\n',
        )
        buffer = io.StringIO()
        with patch("sys.stdout", buffer):
            db = ApplicationsDatabase()
        output = buffer.getvalue()

        assert output.count("neither files nor an action") == 1
        assert "skipped" in output
        # The bad block is skipped entirely: only the top-level unit remains.
        units = db.get_units("neither")
        assert len(units) == 1
        assert [local for local, _ in units[0].mappings] == [".top"]

    def test_a_path_declared_by_two_units_is_deduplicated_config_wide(self):
        """At the parent commit, the file/mapping lists were config-wide, so a
        path declared twice in one config could not appear twice. Per-unit
        lists must not reintroduce that duplicate.
        """
        self._write(
            "dup",
            'name = "Dup"\nfiles = [".dup"]\n\n[[block]]\nfiles = [".dup"]\n',
        )
        db = ApplicationsDatabase()

        assert db.get_files("dup") == [".dup"]
        # The first unit to declare the path (the top-level unit, slot 0)
        # keeps it.
        assert db.get_file_mappings("dup") == [(".dup", ".dup", 0)]
