import io
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

import pytest

from mackup_ng import update, utils
from mackup_ng.main import main


class TestCLI(unittest.TestCase):
    """Test suite for CLI sync and removal workflows."""

    def setUp(self):
        """Set up test environment before each test."""
        # Create temporary directories for testing
        self.test_home = tempfile.mkdtemp(prefix="mackup_test_home_")
        self.test_storage = tempfile.mkdtemp(prefix="mackup_test_storage_")
        self.mackup_folder = os.path.join(self.test_storage, "Mackup")

        # Store original HOME
        self.original_home = os.environ.get("HOME")
        self.original_xdg = os.environ.get("XDG_CONFIG_HOME")
        self.original_xdg_cache = os.environ.get("XDG_CACHE_HOME")

        # Set HOME to our test directory
        os.environ["HOME"] = self.test_home
        os.environ["XDG_CONFIG_HOME"] = os.path.join(self.test_home, ".config")
        os.environ["XDG_CACHE_HOME"] = os.path.join(self.test_home, ".cache")

        # Create test config file
        self.config_path = os.path.join(self.test_home, ".mackup.cfg")
        with open(self.config_path, "w") as f:
            f.write("[storage]\n")
            f.write("engine = file_system\n")
            f.write(f"path = {self.test_storage}\n")
            f.write("directory = Mackup\n")
            f.write("\n")
            f.write("[applications_to_sync]\n")
            f.write("test-app\n")

        # Create a test application config in the apps database
        self.test_app_name = "test-app"
        self.test_file_name = ".testrc"
        self.test_file_path = os.path.join(self.test_home, self.test_file_name)

        # Create test file with content
        with open(self.test_file_path, "w") as f:
            f.write("test_config=value\n")

        # Create custom application config
        self.custom_apps_dir = os.path.join(self.test_home, ".mackup", "applications")
        os.makedirs(self.custom_apps_dir, exist_ok=True)

        self.custom_app_config = os.path.join(self.custom_apps_dir, "test-app.toml")
        with open(self.custom_app_config, "w") as f:
            f.write("[application]\n")
            f.write(f'name = "{self.test_app_name}"\n')
            f.write("files = [\n")
            f.write(f'    "{self.test_file_name}",\n')
            f.write("]\n")

        # Mock the update.fetch_latest to prevent any tests from touching the network.
        # Individual tests can override this with their own patch for testing.
        self.fetch_patcher = patch(
            "mackup_ng.update.fetch_latest", return_value=None,
        )
        self.fetch_patcher.start()
        self.addCleanup(self.fetch_patcher.stop)

        # Force yes to all prompts
        utils.FORCE_YES = True
        utils.FORCE_NO = False
        utils.CAN_RUN_AS_ROOT = False

    def tearDown(self):
        """Clean up test environment after each test."""
        # Restore original HOME
        if self.original_home:
            os.environ["HOME"] = self.original_home
        else:
            os.environ.pop("HOME", None)

        # Restore original XDG_CONFIG_HOME
        if self.original_xdg:
            os.environ["XDG_CONFIG_HOME"] = self.original_xdg
        else:
            os.environ.pop("XDG_CONFIG_HOME", None)

        # Restore original XDG_CACHE_HOME
        if self.original_xdg_cache:
            os.environ["XDG_CACHE_HOME"] = self.original_xdg_cache
        else:
            os.environ.pop("XDG_CACHE_HOME", None)

        # Clean up temporary directories
        if os.path.exists(self.test_home):
            shutil.rmtree(self.test_home)
        if os.path.exists(self.test_storage):
            shutil.rmtree(self.test_storage)

        # Reset utils flags
        utils.FORCE_YES = False
        utils.FORCE_NO = False
        utils.CAN_RUN_AS_ROOT = False

    def test_sync_updates_local_when_backup_is_newer(self):
        """Test sync restores local file when backup is newer."""
        os.makedirs(self.mackup_folder, exist_ok=True)
        backed_up_file = os.path.join(self.mackup_folder, self.test_file_name)

        # Make backup newer and different
        with open(backed_up_file, "w") as f:
            f.write("backup_newer_value\n")
        os.utime(backed_up_file, None)

        # Ensure local file is older
        os.utime(self.test_file_path, (100, 100))

        with patch("sys.argv", ["mackup", "sync"]):
            main()

        with open(self.test_file_path) as f:
            assert f.read() == "backup_newer_value\n"

    def test_sync_updates_backup_when_local_is_newer(self):
        """Test sync backs up local file when local file is newer."""
        with patch("sys.argv", ["mackup", "sync"]):
            main()

        backed_up_file = os.path.join(self.mackup_folder, self.test_file_name)
        assert os.path.exists(backed_up_file)

        # Make local newer and different
        with open(self.test_file_path, "w") as f:
            f.write("local_newer_value\n")
        os.utime(self.test_file_path, None)

        # Ensure backup file is older
        os.utime(backed_up_file, (100, 100))

        with patch("sys.argv", ["mackup", "sync"]):
            main()

        with open(backed_up_file) as f:
            assert f.read() == "local_newer_value\n"

    def test_rm_deletes_local_and_backup_and_records_tombstone(self):
        """Test rm deletes a managed path and records it in backup storage."""
        with patch("sys.argv", ["mackup", "sync"]):
            main()

        backed_up_file = os.path.join(self.mackup_folder, self.test_file_name)
        assert os.path.exists(self.test_file_path)
        assert os.path.exists(backed_up_file)

        with patch("sys.argv", ["mackup", "rm", self.test_file_name]):
            main()

        assert not os.path.exists(self.test_file_path)
        assert not os.path.exists(backed_up_file)

        deletions_file = os.path.join(self.mackup_folder, ".mackup-deletions")
        with open(deletions_file) as f:
            assert f.read().splitlines() == [self.test_file_name]

    def test_rm_deletes_multiple_paths_from_current_directory(self):
        """Test rm accepts multiple paths relative to the current directory."""
        nested_dir = os.path.join(self.test_home, ".ssh")
        os.makedirs(nested_dir, exist_ok=True)
        first_name = "config.bak.codex-20260430"
        second_name = "config.d"
        first_path = os.path.join(nested_dir, first_name)
        second_path = os.path.join(nested_dir, second_name)
        with open(first_path, "w") as f:
            f.write("first\n")
        os.makedirs(second_path, exist_ok=True)
        with open(os.path.join(second_path, "host"), "w") as f:
            f.write("second\n")

        with open(self.custom_app_config, "w") as f:
            f.write("[application]\n")
            f.write(f'name = "{self.test_app_name}"\n')
            f.write("files = [\n")
            f.write(f'    ".ssh/{first_name}",\n')
            f.write(f'    ".ssh/{second_name}",\n')
            f.write("]\n")

        with patch("sys.argv", ["mackup", "sync"]):
            main()

        original_cwd = os.getcwd()
        os.chdir(nested_dir)
        try:
            with patch("sys.argv", ["mackup", "rm", first_name, second_name]):
                main()
        finally:
            os.chdir(original_cwd)

        assert not os.path.exists(first_path)
        assert not os.path.exists(second_path)
        assert not os.path.exists(os.path.join(self.mackup_folder, ".ssh", first_name))
        assert not os.path.exists(os.path.join(self.mackup_folder, ".ssh", second_name))

        deletions_file = os.path.join(self.mackup_folder, ".mackup-deletions")
        with open(deletions_file) as f:
            assert f.read().splitlines() == [
                f".ssh/{first_name}",
                f".ssh/{second_name}",
            ]

    def test_rm_deletes_file_inside_managed_directory_from_current_directory(self):
        """Test rm accepts nested files when their parent directory is managed."""
        nested_dir = os.path.join(self.test_home, ".ssh")
        os.makedirs(nested_dir, exist_ok=True)
        nested_name = "config.bak.codex-20260430"
        nested_path = os.path.join(nested_dir, nested_name)
        with open(nested_path, "w") as f:
            f.write("nested\n")

        with open(self.custom_app_config, "w") as f:
            f.write("[application]\n")
            f.write(f'name = "{self.test_app_name}"\n')
            f.write("files = [\n")
            f.write('    ".ssh",\n')
            f.write("]\n")

        with patch("sys.argv", ["mackup", "sync"]):
            main()

        original_cwd = os.getcwd()
        os.chdir(nested_dir)
        try:
            stdout = io.StringIO()
            with patch("sys.argv", ["mackup", "rm", nested_name]), patch(
                "sys.stdout",
                stdout,
            ):
                main()
        finally:
            os.chdir(original_cwd)

        assert not os.path.exists(nested_path)
        assert not os.path.exists(os.path.join(self.mackup_folder, ".ssh", nested_name))

        deletions_file = os.path.join(self.mackup_folder, ".mackup-deletions")
        with open(deletions_file) as f:
            assert f.read().splitlines() == [f".ssh/{nested_name}"]
        assert f"Deleted .ssh/{nested_name} ({self.test_app_name})" in stdout.getvalue()

    def test_sync_applies_deletion_tombstone(self):
        """Test sync deletes files listed in the backup-side deletion log."""
        os.makedirs(self.mackup_folder, exist_ok=True)
        backed_up_file = os.path.join(self.mackup_folder, self.test_file_name)
        with open(backed_up_file, "w") as f:
            f.write("backup_config=value\n")
        with open(os.path.join(self.mackup_folder, ".mackup-deletions"), "w") as f:
            f.write(f"{self.test_file_name}\n")

        assert os.path.exists(self.test_file_path)
        assert os.path.exists(backed_up_file)

        with patch("sys.argv", ["mackup", "sync"]):
            main()

        assert not os.path.exists(self.test_file_path)
        assert not os.path.exists(backed_up_file)

    def test_sync_with_folder(self):
        """Test that mackup sync works with folders, not just files."""
        # Create a test folder with a file inside
        test_folder_name = ".test_folder"
        test_folder_path = os.path.join(self.test_home, test_folder_name)
        os.makedirs(test_folder_path, exist_ok=True)

        test_file_in_folder = os.path.join(test_folder_path, "config.txt")
        with open(test_file_in_folder, "w") as f:
            f.write("folder_config=value\n")

        # Update custom app config to include the folder
        with open(self.custom_app_config, "w") as f:
            f.write("[application]\n")
            f.write(f'name = "{self.test_app_name}"\n')
            f.write("files = [\n")
            f.write(f'    "{self.test_file_name}",\n')
            f.write(f'    "{test_folder_name}",\n')
            f.write("]\n")

        # Run sync
        with patch("sys.argv", ["mackup", "sync"]):
            main()

        # Check that folder was copied
        backed_up_folder = os.path.join(self.mackup_folder, test_folder_name)
        assert os.path.exists(backed_up_folder)
        assert os.path.isdir(backed_up_folder)

        # Check that file inside folder was copied
        backed_up_file_in_folder = os.path.join(backed_up_folder, "config.txt")
        assert os.path.exists(backed_up_file_in_folder)

        # Verify content
        with open(backed_up_file_in_folder) as f:
            assert f.read() == "folder_config=value\n"

    def test_force_and_force_no_are_mutually_exclusive(self):
        """Passing --force and --force-no together should fail fast."""
        with patch("sys.argv", ["mackup", "--force", "--force-no", "sync"]):
            with pytest.raises(SystemExit) as context:
                main()

            assert (
                str(context.value)
                == "Options --force and --force-no are mutually exclusive."
            )

    def test_sync_runs_post_block_after_files(self):
        """A config that syncs a file AND chmods it via a post block."""
        target = os.path.join(self.test_home, ".secretrc")
        with open(target, "w") as f:
            f.write("k\n")
        os.chmod(target, 0o644)
        with open(self.custom_app_config, "w") as f:
            f.write(
                f'name = "{self.test_app_name}"\n'
                'files = [".secretrc"]\n'
                "[[block]]\n"
                "[block.chmod]\n"
                'path = "~/.secretrc"\n'
                'mode = "600"\n',
            )
        with patch("sys.argv", ["mackup", "sync"]):
            main()
        assert os.stat(target).st_mode & 0o777 == 0o600

    def test_list_hides_block_only(self):
        """Block-only configs (no sync files) are hidden from `list`."""
        with open(os.path.join(self.custom_apps_dir, "hookonly.toml"), "w") as f:
            f.write('[run]\ncommands = ["true"]\n')
        buf = io.StringIO()
        with patch("sys.argv", ["mackup", "list"]), \
                patch("sys.stdout", buf):
            main()
        assert "hookonly" not in buf.getvalue()

    def test_apply_runs_blocks_without_sync(self):
        """`mackup apply` runs blocks and does not sync files."""
        out = os.path.join(self.test_home, ".applied")
        with open(os.path.join(self.custom_apps_dir, "hook.toml"), "w") as f:
            f.write(f'[run]\ncommands = [\'touch "{out}"\']\n')
        with patch("sys.argv", ["mackup", "apply"]):
            main()
        assert os.path.isfile(out)

    def _write_custom_app(self, app_id, body):
        path = os.path.join(self.custom_apps_dir, f"{app_id}.toml")
        with open(path, "w") as handle:
            handle.write(f'name = "{app_id}"\n{body}')
        with open(self.config_path, "a") as handle:
            handle.write(f"{app_id}\n")

    def test_sync_fans_backup_out_to_two_destinations(self):
        self._write_custom_app(
            "fanout",
            '[mapped_files]\n'
            '".work.rc" = ".shared.rc"\n'
            '".home.rc" = ".shared.rc"\n',
        )
        source = os.path.join(self.mackup_folder, ".shared.rc")
        os.makedirs(self.mackup_folder, exist_ok=True)
        with open(source, "w") as handle:
            handle.write("shared=1\n")

        with patch("sys.argv", ["mackup", "sync"]):
            main()

        for name in (".work.rc", ".home.rc"):
            with open(os.path.join(self.test_home, name)) as handle:
                assert handle.read() == "shared=1\n"

    def test_later_config_overrides_the_destination(self):
        self._write_custom_app("aaa-base", 'files = [".overridden"]\n')
        self._write_custom_app(
            "zzz-override",
            '[mapped_files]\n".overridden" = ".from-work"\n',
        )
        os.makedirs(self.mackup_folder, exist_ok=True)
        with open(os.path.join(self.mackup_folder, ".from-work"), "w") as handle:
            handle.write("work\n")
        with open(os.path.join(self.mackup_folder, ".overridden"), "w") as handle:
            handle.write("base\n")

        with patch("sys.argv", ["mackup", "sync"]):
            main()

        with open(os.path.join(self.test_home, ".overridden")) as handle:
            assert handle.read() == "work\n"
        # the evicted source keeps its content and is left alone
        with open(os.path.join(self.mackup_folder, ".overridden")) as handle:
            assert handle.read() == "base\n"

    def test_tombstoned_destination_stays_removed_but_group_survives(self):
        self._write_custom_app(
            "fanout",
            '[mapped_files]\n'
            '".work.rc" = ".shared.rc"\n'
            '".home.rc" = ".shared.rc"\n',
        )
        os.makedirs(self.mackup_folder, exist_ok=True)
        with open(os.path.join(self.mackup_folder, ".shared.rc"), "w") as handle:
            handle.write("shared=1\n")
        with open(os.path.join(self.test_home, ".work.rc"), "w") as handle:
            handle.write("local-work=1\n")
        with open(
            os.path.join(self.mackup_folder, ".mackup-deletions"), "w",
        ) as handle:
            handle.write(".work.rc\n")

        with patch("sys.argv", ["mackup", "sync"]):
            main()

        assert not os.path.exists(os.path.join(self.test_home, ".work.rc"))
        assert os.path.exists(os.path.join(self.test_home, ".home.rc"))
        assert os.path.exists(os.path.join(self.mackup_folder, ".shared.rc"))

    def test_verbose_sync_reports_evictions_and_orphans(self):
        self._write_custom_app("aaa-base", 'files = [".overridden"]\n')
        self._write_custom_app(
            "zzz-override",
            '[mapped_files]\n".overridden" = ".from-work"\n',
        )
        os.makedirs(self.mackup_folder, exist_ok=True)
        with open(os.path.join(self.mackup_folder, ".from-work"), "w") as handle:
            handle.write("work\n")

        buffer = io.StringIO()
        with patch("sys.stdout", buffer), patch(
            "sys.argv", ["mackup", "-v", "sync"],
        ):
            main()
        output = buffer.getvalue()
        assert (
            ".overridden <- .overridden (aaa-base) evicted by zzz-override"
            in output
        )
        assert ".overridden has no destination, left untouched" in output

    def test_verbose_sync_does_not_report_identical_redeclarations(self):
        """Re-declaring the same mapping changes nothing, so it is not noise."""
        self._write_custom_app("aaa-base", 'files = [".dupfile"]\n')
        self._write_custom_app("zzz-same", 'files = [".dupfile"]\n')
        with open(os.path.join(self.test_home, ".dupfile"), "w") as handle:
            handle.write("dup\n")

        buffer = io.StringIO()
        with patch("sys.stdout", buffer), patch(
            "sys.argv", ["mackup", "-v", "sync"],
        ):
            main()
        output = buffer.getvalue()
        assert "evicted by" not in output
        assert "no destination" not in output

    def test_fanout_group_is_synced_in_the_slot_of_the_winning_config(self):
        """A group shared by two configs belongs to the one that won it."""
        self._write_custom_app(
            "aaa-first", '[mapped_files]\n".work.rc" = ".shared.rc"\n',
        )
        self._write_custom_app(
            "zzz-last", '[mapped_files]\n".home.rc" = ".shared.rc"\n',
        )
        os.makedirs(self.mackup_folder, exist_ok=True)
        with open(os.path.join(self.mackup_folder, ".shared.rc"), "w") as handle:
            handle.write("shared=1\n")

        buffer = io.StringIO()
        with patch("sys.stdout", buffer), patch("sys.argv", ["mackup", "sync"]):
            main()
        output = buffer.getvalue()

        for name in (".work.rc", ".home.rc"):
            assert os.path.exists(os.path.join(self.test_home, name))
        assert "Restored zzz-last" in output
        assert "Skipped aaa-first" in output

    def test_sync_reports_failed_tombstone_deletions(self):
        """A tombstone that cannot be deleted is not swallowed by the summary."""
        if os.geteuid() == 0:
            self.skipTest("root ignores permission bits")
        locked_dir = os.path.join(self.test_home, ".locked")
        os.makedirs(locked_dir, exist_ok=True)
        with open(os.path.join(locked_dir, "rc"), "w") as handle:
            handle.write("locked\n")
        self._write_custom_app("locked", 'files = [".locked/rc"]\n')

        with patch("sys.argv", ["mackup", "sync"]):
            main()
        with open(
            os.path.join(self.mackup_folder, ".mackup-deletions"), "w",
        ) as handle:
            handle.write(".locked/rc\n")

        os.chmod(locked_dir, 0o500)
        buffer = io.StringIO()
        try:
            with patch("sys.stdout", buffer), patch("sys.argv", ["mackup", "sync"]):
                main()
        finally:
            os.chmod(locked_dir, 0o700)

        assert "Failed to delete 1 tombstoned path(s)" in buffer.getvalue()

    def test_sync_survives_a_file_directory_type_clash(self):
        """A type clash resolves in place instead of aborting the whole run."""
        self._write_custom_app("aaa-clash", 'files = [".cfgdir"]\n')
        self._write_custom_app("zzz-after", 'files = [".afterrc"]\n')
        os.makedirs(self.mackup_folder, exist_ok=True)
        backup_dir = os.path.join(self.mackup_folder, ".cfgdir")
        os.makedirs(backup_dir, exist_ok=True)
        with open(os.path.join(backup_dir, "inside.txt"), "w") as handle:
            handle.write("from-backup\n")
        os.utime(os.path.join(backup_dir, "inside.txt"), (5000, 5000))
        os.utime(backup_dir, (5000, 5000))
        local_file = os.path.join(self.test_home, ".cfgdir")
        with open(local_file, "w") as handle:
            handle.write("stale\n")
        os.utime(local_file, (1000, 1000))
        with open(os.path.join(self.test_home, ".afterrc"), "w") as handle:
            handle.write("after\n")

        with patch("sys.argv", ["mackup", "sync"]):
            main()

        assert os.path.isdir(local_file)
        with open(os.path.join(local_file, "inside.txt")) as handle:
            assert handle.read() == "from-backup\n"
        # The app sorted after the clash still got its turn.
        assert os.path.exists(os.path.join(self.mackup_folder, ".afterrc"))

    def test_show_reports_the_config_that_overrode_a_destination(self):
        self._write_custom_app("aaa-base", 'files = [".overridden"]\n')
        self._write_custom_app(
            "zzz-override",
            '[mapped_files]\n".overridden" = ".from-work"\n',
        )
        buffer = io.StringIO()
        with patch("sys.stdout", buffer), patch(
            "sys.argv", ["mackup", "show", "aaa-base"],
        ):
            main()
        assert (
            ".overridden <- .overridden (overridden by zzz-override)"
            in buffer.getvalue()
        )

    def test_show_marks_a_config_excluded_from_sync(self):
        """`show` resolves the sync plan, which holds nothing for a skipped app."""
        path = os.path.join(self.custom_apps_dir, "unselected.toml")
        with open(path, "w") as handle:
            handle.write('name = "unselected"\nfiles = [".unselectedrc"]\n')
        buffer = io.StringIO()
        with patch("sys.stdout", buffer), patch(
            "sys.argv", ["mackup", "show", "unselected"],
        ):
            main()
        assert (
            ".unselectedrc <- .unselectedrc (not selected for sync)"
            in buffer.getvalue()
        )

    def test_show_reports_fanout_destinations(self):
        self._write_custom_app(
            "fanout",
            '[mapped_files]\n'
            '".work.rc" = ".shared.rc"\n'
            '".home.rc" = ".shared.rc"\n',
        )
        buffer = io.StringIO()
        with patch("sys.stdout", buffer), patch(
            "sys.argv", ["mackup", "show", "fanout"],
        ):
            main()
        output = buffer.getvalue()
        assert ".work.rc <- .shared.rc" in output
        assert "fanout: 2 destinations" in output

    def test_sync_reports_skipped_for_config_fully_evicted(self):
        """A selected config whose only pair loses its destination still reports."""
        self._write_custom_app("aaa-base", 'files = [".overridden"]\n')
        self._write_custom_app(
            "zzz-override",
            '[mapped_files]\n".overridden" = ".from-work"\n',
        )
        os.makedirs(self.mackup_folder, exist_ok=True)
        with open(os.path.join(self.mackup_folder, ".from-work"), "w") as handle:
            handle.write("work\n")
        # Without a real source the config would report Skipped anyway, which
        # would make the assertion below prove nothing.
        with open(os.path.join(self.mackup_folder, ".overridden"), "w") as handle:
            handle.write("base\n")

        buffer = io.StringIO()
        with patch("sys.stdout", buffer), patch("sys.argv", ["mackup", "sync"]):
            main()
        output = buffer.getvalue()
        assert "Skipped aaa-base" in output

    def test_sync_reports_a_newer_release(self):
        buffer = io.StringIO()
        with patch("sys.stdout", buffer), patch(
            "mackup_ng.update.fetch_latest", return_value="99.0.0",
        ), patch("sys.argv", ["mackup", "sync"]):
            main()
        assert "99.0.0 available" in buffer.getvalue()

    def test_sync_says_nothing_when_up_to_date(self):
        buffer = io.StringIO()
        with patch("sys.stdout", buffer), patch(
            "mackup_ng.update.fetch_latest", return_value="0.0.1",
        ), patch("sys.argv", ["mackup", "sync"]):
            main()
        assert "available" not in buffer.getvalue()

    def test_dry_run_neither_fetches_nor_writes_the_cache(self):
        calls = []

        def fetch(timeout=2.0):
            calls.append(timeout)
            return "99.0.0"

        with patch("mackup_ng.update.fetch_latest", fetch), patch(
            "sys.argv", ["mackup", "-n", "sync"],
        ):
            main()
        assert calls == []
        assert not os.path.exists(update.cache_path())


if __name__ == "__main__":
    unittest.main()
