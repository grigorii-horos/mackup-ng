"""A config's top-level [when] gates its sync entries and its blocks."""

import io
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from mackup_ng import utils
from mackup_ng.main import main


class TestConfigLevelConditions(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="mackup_when_home_")
        self.storage = tempfile.mkdtemp(prefix="mackup_when_storage_")
        self.mackup_folder = os.path.join(self.storage, "Mackup")
        os.makedirs(self.mackup_folder, exist_ok=True)
        self._orig = {
            key: os.environ.get(key)
            for key in ("HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME")
        }
        os.environ["HOME"] = self.home
        os.environ["XDG_CONFIG_HOME"] = os.path.join(self.home, ".config")
        os.environ["XDG_STATE_HOME"] = os.path.join(self.home, ".local", "state")
        os.environ["XDG_CACHE_HOME"] = os.path.join(self.home, ".cache")

        with open(os.path.join(self.home, ".mackup.cfg"), "w") as handle:
            handle.write(
                "[storage]\nengine = file_system\n"
                f"path = {self.storage}\ndirectory = Mackup\n\n"
                "[applications_to_sync]\naaa-base\nzzz-override\ngated-blocks\n",
            )
        self.apps_dir = os.path.join(self.home, ".mackup", "applications")
        os.makedirs(self.apps_dir, exist_ok=True)
        utils.FORCE_YES = True

        # Mock the update.fetch_latest to prevent any tests from touching the network.
        self.fetch_patcher = patch(
            "mackup_ng.update.fetch_latest",
            return_value=None,
        )
        self.fetch_patcher.start()
        self.addCleanup(self.fetch_patcher.stop)

    def tearDown(self):
        for key, orig in self._orig.items():
            if orig is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = orig
        shutil.rmtree(self.home, ignore_errors=True)
        shutil.rmtree(self.storage, ignore_errors=True)
        utils.FORCE_YES = False

    def _write_app(self, name, body):
        with open(os.path.join(self.apps_dir, f"{name}.toml"), "w") as handle:
            handle.write(f'name = "{name}"\n{body}')

    def _write_backup(self, relative, content):
        path = os.path.join(self.mackup_folder, relative)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as handle:
            handle.write(content)

    def _set_marker(self, name):
        markers = os.path.join(
            os.environ["XDG_STATE_HOME"],
            "mackup",
            "markers",
        )
        os.makedirs(markers, exist_ok=True)
        open(os.path.join(markers, name), "a").close()

    def _write_palette_configs(self):
        self._write_app(
            "aaa-base",
            '[mapped_files]\n".colors" = ".palette-default"\n',
        )
        self._write_app(
            "zzz-override",
            '[when]\nmarker = ["eink"]\n\n'
            '[mapped_files]\n".colors" = ".palette-eink"\n',
        )
        self._write_backup(".palette-default", "default\n")
        self._write_backup(".palette-eink", "eink\n")

    def test_override_is_inactive_without_the_marker(self):
        self._write_palette_configs()
        with patch("sys.argv", ["mackup", "sync"]):
            main()
        with open(os.path.join(self.home, ".colors")) as handle:
            assert handle.read() == "default\n"

    def test_override_wins_when_the_marker_is_set(self):
        self._write_palette_configs()
        self._set_marker("eink")
        with patch("sys.argv", ["mackup", "sync"]):
            main()
        with open(os.path.join(self.home, ".colors")) as handle:
            assert handle.read() == "eink\n"

    def test_gated_out_config_runs_no_blocks(self):
        touched = os.path.join(self.home, "block-ran.txt")
        self._write_app(
            "gated-blocks",
            '[when]\nmarker = ["nope"]\n\n'
            f'[run]\nscript = "touch {touched}"\n\n'
            "[[block]]\n"
            f'[block.run]\nscript = "touch {touched}.two"\n',
        )
        with patch("sys.argv", ["mackup", "sync"]):
            main()
        assert not os.path.exists(touched)
        assert not os.path.exists(f"{touched}.two")

    def test_enabled_config_still_runs_its_blocks(self):
        touched = os.path.join(self.home, "block-ran.txt")
        self._write_app(
            "gated-blocks",
            f'[when]\nnot_marker = ["nope"]\n\n[run]\nscript = "touch {touched}"\n',
        )
        with patch("sys.argv", ["mackup", "sync"]):
            main()
        assert os.path.exists(touched)

    def test_apply_skips_a_gated_out_config(self):
        touched = os.path.join(self.home, "applied.txt")
        self._write_app(
            "gated-blocks",
            f'[when]\nmarker = ["nope"]\n\n[run]\nscript = "touch {touched}"\n',
        )
        with patch("sys.argv", ["mackup", "apply"]):
            main()
        assert not os.path.exists(touched)

    def test_source_orphaned_by_a_gated_out_config_is_untouched(self):
        # Set up only zzz-override to claim .colors <- .palette-eink.
        # With the gate in place, zzz-override is disabled, so .palette-eink
        # has no destination and is an orphan.
        # Without the gate, .palette-eink would be synced to/from .colors.
        self._write_app(
            "zzz-override",
            '[when]\nmarker = ["eink"]\n\n'
            '[mapped_files]\n".colors" = ".palette-eink"\n',
        )
        self._write_backup(".palette-eink", "eink\n")
        # Create ~/.colors locally with different content and a newer mtime.
        # If zzz-override claims .colors, the newer local file would be backed up,
        # overwriting .palette-eink.
        colors_path = os.path.join(self.home, ".colors")
        with open(colors_path, "w") as handle:
            handle.write("local\n")
        backup_eink_path = os.path.join(self.mackup_folder, ".palette-eink")
        backup_mtime = os.path.getmtime(backup_eink_path)
        os.utime(colors_path, (backup_mtime + 10, backup_mtime + 10))
        with patch("sys.argv", ["mackup", "sync"]):
            main()
        # With the gate in place, .palette-eink is never claimed or synced,
        # so it remains untouched with its original content.
        assert os.path.exists(backup_eink_path)
        with open(backup_eink_path) as handle:
            assert handle.read() == "eink\n"

    def test_show_reports_unmet_conditions(self):
        self._write_palette_configs()
        buffer = io.StringIO()
        with (
            patch("sys.stdout", buffer),
            patch(
                "sys.argv",
                ["mackup", "show", "zzz-override"],
            ),
        ):
            main()
        output = buffer.getvalue()
        assert "conditions not met on this machine" in output
        assert "marker" in output

    def test_show_says_nothing_about_conditions_when_they_hold(self):
        self._write_palette_configs()
        self._set_marker("eink")
        buffer = io.StringIO()
        with (
            patch("sys.stdout", buffer),
            patch(
                "sys.argv",
                ["mackup", "show", "zzz-override"],
            ),
        ):
            main()
        assert "conditions not met" not in buffer.getvalue()

    def test_verbose_sync_reports_the_skipped_config(self):
        self._write_palette_configs()
        buffer = io.StringIO()
        with (
            patch("sys.stdout", buffer),
            patch(
                "sys.argv",
                ["mackup", "-v", "sync"],
            ),
        ):
            main()
        assert "zzz-override: conditions not met on this machine" in buffer.getvalue()

    def test_non_verbose_sync_stays_quiet_about_it(self):
        self._write_palette_configs()
        buffer = io.StringIO()
        with patch("sys.stdout", buffer), patch("sys.argv", ["mackup", "sync"]):
            main()
        assert "conditions not met" not in buffer.getvalue()

    def test_show_reports_only_failing_conditions(self):
        """When a config has multiple conditions, show only the ones that fail."""
        # Set up a config with two conditions:
        # - not_marker = ["absent"] (passes - marker "absent" is not set)
        # - marker = ["eink"] (fails - marker "eink" is not set)
        self._write_app(
            "multi-condition",
            '[when]\nnot_marker = ["absent"]\nmarker = ["eink"]\n\n'
            '[mapped_files]\n".colors" = ".palette"\n',
        )
        self._write_backup(".palette", "palette\n")
        buffer = io.StringIO()
        with (
            patch("sys.stdout", buffer),
            patch(
                "sys.argv",
                ["mackup", "show", "multi-condition"],
            ),
        ):
            main()
        output = buffer.getvalue()
        # Should mention failing condition: marker
        assert "marker=" in output
        # Should NOT mention passing condition: not_marker
        assert "not_marker=" not in output
        # Should still have the standard phrase
        assert "conditions not met on this machine" in output

    def test_verbose_sync_reports_gated_out_configs_unclaimed_source(self):
        # zzz-override is gated out (no marker set) and nothing else declares
        # .palette-eink, so it is an ordinary orphan: reported under -v.
        self._write_palette_configs()
        buffer = io.StringIO()
        with (
            patch("sys.stdout", buffer),
            patch(
                "sys.argv",
                ["mackup", "-v", "sync"],
            ),
        ):
            main()
        assert ".palette-eink has no destination, left untouched" in buffer.getvalue()

    def test_source_claimed_by_an_enabled_config_is_not_reported_as_orphan(self):
        # aaa-base and zzz-override both declare .palette-default; aaa-base is
        # always enabled, so .palette-default is claimed and must NOT be
        # reported as an orphan even though zzz-override (which shares it) is
        # gated out.
        self._write_app(
            "aaa-base",
            '[mapped_files]\n".colors" = ".palette-default"\n',
        )
        self._write_app(
            "zzz-override",
            '[when]\nmarker = ["eink"]\n\n'
            '[mapped_files]\n".other-colors" = ".palette-default"\n',
        )
        self._write_backup(".palette-default", "default\n")
        buffer = io.StringIO()
        with (
            patch("sys.stdout", buffer),
            patch(
                "sys.argv",
                ["mackup", "-v", "sync"],
            ),
        ):
            main()
        assert ".palette-default has no destination" not in buffer.getvalue()

    def test_gated_out_configs_orphan_not_reported_without_verbose(self):
        self._write_palette_configs()
        buffer = io.StringIO()
        with patch("sys.stdout", buffer), patch("sys.argv", ["mackup", "sync"]):
            main()
        assert "has no destination" not in buffer.getvalue()

    def test_gated_out_configs_orphaned_backup_file_stays_on_disk(self):
        self._write_palette_configs()
        buffer = io.StringIO()
        with (
            patch("sys.stdout", buffer),
            patch(
                "sys.argv",
                ["mackup", "-v", "sync"],
            ),
        ):
            main()
        backup_eink_path = os.path.join(self.mackup_folder, ".palette-eink")
        assert os.path.exists(backup_eink_path)
        with open(backup_eink_path) as handle:
            assert handle.read() == "eink\n"
