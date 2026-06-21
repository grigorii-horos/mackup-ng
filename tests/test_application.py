import os
import shutil
import sys
import tempfile
import unittest
from io import StringIO
from unittest.mock import Mock, patch

from mackup_ng.application import ApplicationProfile
from mackup_ng.mackup import Mackup


class TestApplicationProfile(unittest.TestCase):
    def setUp(self):
        """Set up test fixtures."""
        # Create a mock Mackup instance
        self.mock_mackup = Mock(spec=Mackup)
        self.mock_mackup.mackup_folder = tempfile.mkdtemp()

        # Create a temporary home directory
        self.temp_home = tempfile.mkdtemp()

        # Save original HOME and set it to temp directory
        self.original_home = os.environ.get("HOME")
        os.environ["HOME"] = self.temp_home

        # Define test files
        self.test_files = {".testfile", ".testfolder"}

        # Create the ApplicationProfile instance
        self.app_profile = ApplicationProfile(
            mackup=self.mock_mackup,
            files=self.test_files,
            dry_run=False,
            verbose=False,
        )

    def tearDown(self):
        """Clean up test fixtures."""
        # Restore original HOME
        if self.original_home:
            os.environ["HOME"] = self.original_home
        else:
            del os.environ["HOME"]

        # Clean up temporary directories
        if os.path.exists(self.temp_home):
            shutil.rmtree(self.temp_home)
        if os.path.exists(self.mock_mackup.mackup_folder):
            shutil.rmtree(self.mock_mackup.mackup_folder)

    def test_files_are_sorted_for_deterministic_processing(self):
        """Application files should always be processed in sorted order."""
        unsorted_files = {"z-last", "a-first", "m-middle"}
        app_profile = ApplicationProfile(
            mackup=self.mock_mackup,
            files=unsorted_files,
            dry_run=False,
            verbose=False,
        )
        assert app_profile.files == ["a-first", "m-middle", "z-last"]
    def test_sync_files_merges_directories_by_file_mtime(self):
        """Sync should merge directories entry-by-entry based on file mtimes."""
        test_dir = ".testfolder"
        home_dirpath = os.path.join(self.temp_home, test_dir)
        mackup_dirpath = os.path.join(self.mock_mackup.mackup_folder, test_dir)
        os.makedirs(home_dirpath)
        os.makedirs(mackup_dirpath)

        home_newer = os.path.join(home_dirpath, "home_newer.txt")
        backup_newer = os.path.join(home_dirpath, "backup_newer.txt")
        with open(home_newer, "w") as f:
            f.write("home-value")
        with open(backup_newer, "w") as f:
            f.write("home-old-value")

        backup_home_newer = os.path.join(mackup_dirpath, "home_newer.txt")
        backup_backup_newer = os.path.join(mackup_dirpath, "backup_newer.txt")
        with open(backup_home_newer, "w") as f:
            f.write("backup-old-value")
        with open(backup_backup_newer, "w") as f:
            f.write("backup-value")

        # File "home_newer.txt" is newer in home, "backup_newer.txt" newer in backup.
        os.utime(home_newer, (300, 300))
        os.utime(backup_home_newer, (100, 100))
        os.utime(backup_newer, (100, 100))
        os.utime(backup_backup_newer, (300, 300))

        self.app_profile.sync_files()

        with open(os.path.join(home_dirpath, "home_newer.txt")) as f:
            assert f.read() == "home-value"
        with open(os.path.join(mackup_dirpath, "home_newer.txt")) as f:
            assert f.read() == "home-value"

        with open(os.path.join(home_dirpath, "backup_newer.txt")) as f:
            assert f.read() == "backup-value"
        with open(os.path.join(mackup_dirpath, "backup_newer.txt")) as f:
            assert f.read() == "backup-value"

    def test_sync_files_updates_directory_mtime_without_copy(self):
        """Sync should align directory mtime without copying when files are equal."""
        test_dir = ".testfolder"
        home_dirpath = os.path.join(self.temp_home, test_dir)
        mackup_dirpath = os.path.join(self.mock_mackup.mackup_folder, test_dir)
        os.makedirs(home_dirpath)
        os.makedirs(mackup_dirpath)

        home_file = os.path.join(home_dirpath, "same.txt")
        mackup_file = os.path.join(mackup_dirpath, "same.txt")
        with open(home_file, "w") as f:
            f.write("same")
        with open(mackup_file, "w") as f:
            f.write("same")
        os.utime(home_file, (100, 100))
        os.utime(mackup_file, (100, 100))
        os.utime(home_dirpath, (100, 100))
        os.utime(mackup_dirpath, (300, 300))

        with patch("mackup_ng.application.utils.copy") as mock_copy:
            self.app_profile.sync_files()
            mock_copy.assert_not_called()

        assert int(os.path.getmtime(home_dirpath)) == 300

    def test_sync_files_verbose_skips_synced_directory_without_sync_message(self):
        """Verbose sync should show skip (not synchronizing) when directory is already in sync."""
        app_profile_verbose = ApplicationProfile(
            mackup=self.mock_mackup,
            files={".testfolder"},
            dry_run=False,
            verbose=True,
        )

        test_dir = ".testfolder"
        home_dirpath = os.path.join(self.temp_home, test_dir)
        mackup_dirpath = os.path.join(self.mock_mackup.mackup_folder, test_dir)
        os.makedirs(home_dirpath)
        os.makedirs(mackup_dirpath)

        home_file = os.path.join(home_dirpath, "same.txt")
        mackup_file = os.path.join(mackup_dirpath, "same.txt")
        with open(home_file, "w") as f:
            f.write("same")
        with open(mackup_file, "w") as f:
            f.write("same")

        os.utime(home_file, (100, 100))
        os.utime(mackup_file, (100, 100))
        os.utime(home_dirpath, (100, 100))
        os.utime(mackup_dirpath, (100, 100))

        captured_output = StringIO()
        sys.stdout = captured_output
        try:
            app_profile_verbose.sync_files()
        finally:
            sys.stdout = sys.__stdout__

        output = captured_output.getvalue()
        assert "Synchronizing" not in output
        assert "Skipping" in output
        assert "already in sync with" in output

    def test_sync_files_logs_single_action_per_file(self):
        """Test sync emits one action line per file."""
        app_profile_verbose = ApplicationProfile(
            mackup=self.mock_mackup,
            files=self.test_files,
            dry_run=False,
            verbose=True,
        )
        test_file = ".testfile"
        home_filepath = os.path.join(self.temp_home, test_file)
        mackup_filepath = os.path.join(self.mock_mackup.mackup_folder, test_file)

        with open(home_filepath, "w") as f:
            f.write("home content")
        with open(mackup_filepath, "w") as f:
            f.write("backup content")

        os.utime(home_filepath, (200, 200))
        os.utime(mackup_filepath, (100, 100))

        captured_output = StringIO()
        sys.stdout = captured_output
        try:
            app_profile_verbose.sync_files()
        finally:
            sys.stdout = sys.__stdout__

        output = captured_output.getvalue()
        assert "Backing up" in output
        assert home_filepath in output
        assert mackup_filepath in output
        assert "Restoring\n" not in output

    def test_sync_files_ignores_missing_file_on_both_sides(self):
        """Sync should not count a file missing in both home and backup as skipped."""
        app_profile = ApplicationProfile(
            mackup=self.mock_mackup,
            files={".missing-file"},
            dry_run=False,
            verbose=False,
        )

        stats = app_profile.sync_files()

        assert stats["backed_up"] == 0
        assert stats["restored"] == 0
        assert stats["synchronized"] == 0
        assert stats["skipped"] == 0
        assert stats["errors"] == 0


if __name__ == "__main__":
    unittest.main()
