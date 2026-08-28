import io
import os
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch

import pytest

from mackup_ng import utils
from mackup_ng.main import main


class TestInfo(unittest.TestCase):
    """`mackup info <path>` reports how one path relates to the backup."""

    def setUp(self):
        self.test_home = tempfile.mkdtemp(prefix="mackup_info_home_")
        self.test_storage = tempfile.mkdtemp(prefix="mackup_info_storage_")
        self.mackup_folder = os.path.join(self.test_storage, "Mackup")

        self._orig = {
            key: os.environ.get(key)
            for key in ("HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME")
        }
        os.environ["HOME"] = self.test_home
        os.environ["XDG_CONFIG_HOME"] = os.path.join(self.test_home, ".config")
        os.environ["XDG_CACHE_HOME"] = os.path.join(self.test_home, ".cache")
        os.environ["XDG_STATE_HOME"] = os.path.join(self.test_home, ".local", "state")

        self.config_path = os.path.join(self.test_home, ".mackup.cfg")
        self.write_config(["test-app"])

        self.test_file_name = ".testrc"
        self.test_file_path = os.path.join(self.test_home, self.test_file_name)
        with open(self.test_file_path, "w") as handle:
            handle.write("test_config=value\n")

        self.custom_apps_dir = os.path.join(self.test_home, ".mackup", "applications")
        os.makedirs(self.custom_apps_dir, exist_ok=True)
        self.write_app("test-app", "test-app", [self.test_file_name])

        self.fetch_patcher = patch("mackup_ng.update.fetch_latest", return_value=None)
        self.fetch_patcher.start()
        self.addCleanup(self.fetch_patcher.stop)

        utils.FORCE_YES = True
        utils.FORCE_NO = False
        utils.CAN_RUN_AS_ROOT = False

    def tearDown(self):
        for key, value in self._orig.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(self.test_home, ignore_errors=True)
        shutil.rmtree(self.test_storage, ignore_errors=True)
        utils.FORCE_YES = False
        utils.FORCE_NO = False
        utils.CAN_RUN_AS_ROOT = False

    def write_config(self, apps):
        with open(self.config_path, "w") as handle:
            handle.write("[storage]\n")
            handle.write("engine = file_system\n")
            handle.write(f"path = {self.test_storage}\n")
            handle.write("directory = Mackup\n\n")
            handle.write("[applications_to_sync]\n")
            handle.writelines(f"{app}\n" for app in apps)

    def write_app(self, filename, name, files, extra=""):
        path = os.path.join(self.custom_apps_dir, f"{filename}.toml")
        with open(path, "w") as handle:
            handle.write("[application]\n")
            handle.write(f'name = "{name}"\n')
            handle.write("files = [\n")
            handle.writelines(f'    "{entry}",\n' for entry in files)
            handle.write("]\n")
            handle.write(extra)

    def write_mapped_app(self, app_id, body):
        path = os.path.join(self.custom_apps_dir, f"{app_id}.toml")
        with open(path, "w") as handle:
            handle.write(f'name = "{app_id}"\n{body}')

    def run_cli(self, *argv):
        buffer = io.StringIO()
        with patch("sys.stdout", buffer), patch("sys.argv", ["mackup", *argv]):
            main()
        return buffer.getvalue()

    def sync(self):
        with patch("sys.argv", ["mackup", "sync"]):
            main()

    def test_info_names_the_config_that_manages_the_path(self):
        output = self.run_cli("info", self.test_file_name)

        assert "Path: .testrc" in output
        assert "test-app" in output

    def test_info_shows_the_backup_path(self):
        output = self.run_cli("info", self.test_file_name)

        assert os.path.join(self.mackup_folder, self.test_file_name) in output

    def test_info_reports_a_synced_pair_as_in_sync(self):
        self.sync()

        output = self.run_cli("info", self.test_file_name)

        assert "State: in sync" in output

    def test_info_reports_a_local_only_path_as_not_backed_up_yet(self):
        output = self.run_cli("info", self.test_file_name)

        assert "local only" in output

    def test_info_reports_a_backup_only_path_as_pending_restore(self):
        os.makedirs(self.mackup_folder, exist_ok=True)
        with open(os.path.join(self.mackup_folder, self.test_file_name), "w") as handle:
            handle.write("remote\n")
        os.remove(self.test_file_path)

        output = self.run_cli("info", self.test_file_name)

        assert "backup only" in output

    def test_info_reports_which_side_is_newer_when_they_diverge(self):
        self.sync()
        with open(self.test_file_path, "w") as handle:
            handle.write("changed locally\n")
        os.utime(self.test_file_path, None)
        os.utime(os.path.join(self.mackup_folder, self.test_file_name), (100, 100))

        output = self.run_cli("info", self.test_file_name)

        assert "diverged" in output
        assert "local is newer" in output

    def test_info_reports_the_backup_side_as_newer_when_it_is(self):
        self.sync()
        backup = os.path.join(self.mackup_folder, self.test_file_name)
        with open(backup, "w") as handle:
            handle.write("changed remotely\n")
        os.utime(backup, None)
        os.utime(self.test_file_path, (100, 100))

        output = self.run_cli("info", self.test_file_name)

        assert "backup is newer" in output

    def test_info_reports_the_last_sync_recorded_for_the_path(self):
        self.sync()

        output = self.run_cli("info", self.test_file_name)

        assert "Last sync:" in output
        assert "Backed up" in output
        assert time.strftime("%Y-%m-%d") in output

    def test_info_says_the_last_sync_is_unknown_before_any_sync(self):
        output = self.run_cli("info", self.test_file_name)

        assert "Last sync: never recorded" in output

    def test_info_accepts_an_absolute_path(self):
        output = self.run_cli("info", self.test_file_path)

        assert "Path: .testrc" in output

    def test_info_rejects_an_unmanaged_path(self):
        unmanaged = os.path.join(self.test_home, ".not-managed")
        with open(unmanaged, "w") as handle:
            handle.write("x\n")

        buffer = io.StringIO()
        with (
            pytest.raises(SystemExit) as excinfo,
            patch(
                "sys.stdout",
                buffer,
            ),
            patch("sys.argv", ["mackup", "info", unmanaged]),
        ):
            main()

        assert excinfo.value.code == 1
        assert "not managed by any config" in buffer.getvalue()

    def test_info_reports_a_path_inside_a_managed_directory(self):
        managed_dir = os.path.join(self.test_home, ".config", "app")
        os.makedirs(managed_dir, exist_ok=True)
        inner = os.path.join(managed_dir, "settings.ini")
        with open(inner, "w") as handle:
            handle.write("a=1\n")
        self.write_app("dir-app", "dir-app", [".config/app"])
        self.write_config(["test-app", "dir-app"])

        output = self.run_cli("info", inner)

        assert "Path: .config/app/settings.ini" in output
        assert "dir-app" in output
        assert "inside .config/app" in output

    def test_info_reports_a_tombstoned_path_as_removed(self):
        self.sync()
        with patch("sys.argv", ["mackup", "rm", self.test_file_name]):
            main()

        output = self.run_cli("info", self.test_file_name)

        assert "removed by mackup rm" in output

    def test_info_reports_a_config_excluded_from_sync(self):
        self.write_config(["other-app"])
        self.write_app("other-app", "other-app", [".otherrc"])

        output = self.run_cli("info", self.test_file_name)

        assert "not selected for sync" in output

    def test_info_reports_unmet_conditions(self):
        self.write_app(
            "test-app",
            "test-app",
            [self.test_file_name],
            extra='\n[when]\nos = "definitely-not-this-os"\n',
        )

        output = self.run_cli("info", self.test_file_name)

        assert "conditions not met" in output
        assert "os=definitely-not-this-os" in output

    def test_info_names_the_config_that_overrode_the_destination(self):
        self.write_mapped_app(
            "zzz-later",
            f'[mapped_files]\n"{self.test_file_name}" = ".testrc-alt"\n',
        )
        self.write_config(["test-app", "zzz-later"])

        output = self.run_cli("info", self.test_file_name)

        assert "zzz-later" in output
        assert ".testrc-alt" in output

    def test_info_reports_fanout_when_one_source_feeds_several_paths(self):
        self.write_mapped_app(
            "fan-app",
            '[mapped_files]\n".fan-a" = ".shared"\n".fan-b" = ".shared"\n',
        )
        self.write_config(["test-app", "fan-app"])

        output = self.run_cli("info", ".fan-a")

        assert "fanout" in output.lower()
        assert ".fan-b" in output

    def test_info_reports_a_managed_directory(self):
        managed_dir = os.path.join(self.test_home, ".config", "app")
        os.makedirs(managed_dir, exist_ok=True)
        with open(os.path.join(managed_dir, "settings.ini"), "w") as handle:
            handle.write("a=1\n")
        self.write_app("dir-app", "dir-app", [".config/app"])
        self.write_config(["test-app", "dir-app"])
        self.sync()

        output = self.run_cli("info", ".config/app")

        assert "State: in sync" in output

    def test_info_reports_how_many_entries_differ_in_a_directory(self):
        managed_dir = os.path.join(self.test_home, ".config", "app")
        os.makedirs(managed_dir, exist_ok=True)
        with open(os.path.join(managed_dir, "settings.ini"), "w") as handle:
            handle.write("a=1\n")
        self.write_app("dir-app", "dir-app", [".config/app"])
        self.write_config(["test-app", "dir-app"])
        self.sync()
        with open(os.path.join(managed_dir, "extra.ini"), "w") as handle:
            handle.write("b=2\n")

        output = self.run_cli("info", ".config/app")

        assert "1 entry differs" in output

    def test_info_accepts_several_paths(self):
        self.write_app("test-app", "test-app", [self.test_file_name, ".otherrc"])
        with open(os.path.join(self.test_home, ".otherrc"), "w") as handle:
            handle.write("other\n")

        output = self.run_cli("info", self.test_file_name, ".otherrc")

        assert "Path: .testrc" in output
        assert "Path: .otherrc" in output


if __name__ == "__main__":
    unittest.main()
