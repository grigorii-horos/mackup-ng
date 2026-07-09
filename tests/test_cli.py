import io
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

import pytest

from mackup_ng import utils
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

        # Set HOME to our test directory
        os.environ["HOME"] = self.test_home
        os.environ["XDG_CONFIG_HOME"] = os.path.join(self.test_home, ".config")

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

        self.custom_app_config = os.path.join(self.custom_apps_dir, "test-app.cfg")
        with open(self.custom_app_config, "w") as f:
            f.write("[application]\n")
            f.write(f"name = {self.test_app_name}\n")
            f.write("\n")
            f.write("[configuration_files]\n")
            f.write(f"{self.test_file_name}\n")

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
            f.write(f"name = {self.test_app_name}\n")
            f.write("\n")
            f.write("[configuration_files]\n")
            f.write(f".ssh/{first_name}\n")
            f.write(f".ssh/{second_name}\n")

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
            f.write(f"name = {self.test_app_name}\n")
            f.write("\n")
            f.write("[configuration_files]\n")
            f.write(".ssh\n")

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
            f.write(f"name = {self.test_app_name}\n")
            f.write("\n")
            f.write("[configuration_files]\n")
            f.write(f"{self.test_file_name}\n")
            f.write(f"{test_folder_name}\n")

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


if __name__ == "__main__":
    unittest.main()
