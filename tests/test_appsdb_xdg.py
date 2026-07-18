"""Tests for ApplicationsDatabase XDG support."""

import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from mackup_ng.appsdb import ApplicationsDatabase


class TestApplicationsDatabaseXDG(unittest.TestCase):
    """Test XDG Base Directory support for custom applications."""

    def setUp(self):
        """Set up test fixtures."""
        realpath = os.path.dirname(os.path.realpath(__file__))
        self.fixtures_path = os.path.join(realpath, "fixtures")
        self._original_home = os.environ.get("HOME")
        self._original_xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
        os.environ["HOME"] = self.fixtures_path

        # Clear XDG_CONFIG_HOME to ensure clean state
        os.environ.pop("XDG_CONFIG_HOME", None)

    def tearDown(self):
        """Restore environment variables modified during tests."""
        if self._original_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._original_home

        if self._original_xdg_config_home is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = self._original_xdg_config_home
    def test_legacy_custom_apps_dir(self):
        """Test that legacy ~/.mackup/ directory is found."""
        # Don't set XDG_CONFIG_HOME, only legacy should be found
        config_files = ApplicationsDatabase.get_config_files()
        filenames = {os.path.basename(f) for f in config_files}

        assert "legacy-test-app.toml" in filenames

    def test_xdg_custom_apps_dir(self):
        """Test that XDG custom apps directory is found."""
        xdg_config = os.path.join(self.fixtures_path, "xdg-config-home")
        os.environ["XDG_CONFIG_HOME"] = xdg_config

        config_files = ApplicationsDatabase.get_config_files()
        filenames = {os.path.basename(f) for f in config_files}

        assert "xdg-test-app.toml" in filenames

    def test_legacy_takes_priority_over_xdg(self):
        """Test that legacy directory takes priority when same app exists."""
        xdg_config = os.path.join(self.fixtures_path, "xdg-config-home")
        os.environ["XDG_CONFIG_HOME"] = xdg_config

        config_files = ApplicationsDatabase.get_config_files()

        # Find the priority-test-app.cfg file
        priority_files = [f for f in config_files if "priority-test-app.toml" in f]

        # Should only have one file (legacy should win)
        assert len(priority_files) == 1

        # Should be from legacy directory
        assert ".mackup" in priority_files[0]
        assert "xdg-config-home" not in priority_files[0]

    def test_both_directories_merged(self):
        """Test that apps from both directories are available."""
        xdg_config = os.path.join(self.fixtures_path, "xdg-config-home")
        os.environ["XDG_CONFIG_HOME"] = xdg_config

        config_files = ApplicationsDatabase.get_config_files()
        filenames = {os.path.basename(f) for f in config_files}

        # Both unique apps should be present
        assert "legacy-test-app.toml" in filenames
        assert "xdg-test-app.toml" in filenames

    def test_xdg_default_fallback(self):
        """Test that XDG falls back to ~/.config when XDG_CONFIG_HOME is not set."""
        # Unset XDG_CONFIG_HOME - should fall back to ~/.config
        os.environ.pop("XDG_CONFIG_HOME", None)

        # This test just verifies the code doesn't crash
        # In real scenario, ~/.config/mackup/applications/ would be checked
        config_files = ApplicationsDatabase.get_config_files()

        # Should at least contain stock apps and legacy custom apps
        assert len(config_files) > 0

    def test_applications_database_loads_xdg_apps(self):
        """Test that ApplicationsDatabase correctly loads apps from XDG."""
        xdg_config = os.path.join(self.fixtures_path, "xdg-config-home")
        os.environ["XDG_CONFIG_HOME"] = xdg_config

        db = ApplicationsDatabase()

        # XDG app should be loaded
        assert "xdg-test-app" in db.get_app_names()
        assert db.get_name("xdg-test-app") == "XDG Test App"

    def test_applications_database_priority_loads_legacy(self):
        """Test ApplicationsDatabase loads legacy version when app exists."""
        xdg_config = os.path.join(self.fixtures_path, "xdg-config-home")
        os.environ["XDG_CONFIG_HOME"] = xdg_config

        db = ApplicationsDatabase()

        # Priority app should load the legacy version
        assert "priority-test-app" in db.get_app_names()
        assert db.get_name("priority-test-app") == "Priority Test App Legacy"

    def test_applications_database_expands_braces_in_config_paths(self):
        """Brace groups in app cfg paths should expand into multiple files."""
        temp_home = tempfile.mkdtemp()
        temp_xdg = os.path.join(temp_home, ".config")
        legacy_apps_dir = os.path.join(temp_home, ".mackup", "applications")
        xdg_apps_dir = os.path.join(temp_xdg, "mackup", "applications")
        os.makedirs(legacy_apps_dir, exist_ok=True)
        os.makedirs(xdg_apps_dir, exist_ok=True)

        cfg_path = os.path.join(legacy_apps_dir, "brace-expand-test.toml")
        with open(cfg_path, "w") as f:
            f.write(
                "[application]\n"
                'name = "Brace Expand Test"\n'
                "files = [\n"
                '    "${MACKUP_XDG_CONFIG}/app/{config1.json,config2.json}",\n'
                '    "${MACKUP_XDG_DATA}/{one,two}/state.db",\n'
                '    "${MACKUP_XDG_CONFIG}/myapp/{prefs.toml,theme.toml}",\n'
                "]\n",
            )

        old_home = os.environ.get("HOME")
        old_xdg = os.environ.get("XDG_CONFIG_HOME")
        try:
            os.environ["HOME"] = temp_home
            os.environ["XDG_CONFIG_HOME"] = temp_xdg

            db = ApplicationsDatabase()
            files = db.get_files("brace-expand-test")

            assert ".config/app/config1.json" in files
            assert ".config/app/config2.json" in files
            assert ".local/share/one/state.db" in files
            assert ".local/share/two/state.db" in files
            assert ".config/myapp/prefs.toml" in files
            assert ".config/myapp/theme.toml" in files
        finally:
            if old_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = old_home
            if old_xdg is None:
                os.environ.pop("XDG_CONFIG_HOME", None)
            else:
                os.environ["XDG_CONFIG_HOME"] = old_xdg
            shutil.rmtree(temp_home)

    def test_platform_selector_resolves_for_current_platform(self):
        """Platform selector syntax should choose the platform-specific path."""
        with patch("mackup_ng.appsdb.platform.system", return_value="Linux"):
            assert (
                ApplicationsDatabase._resolve_platform_selectors(
                    "[linux:.config/,mac:Library/Application Support,windows:AppData/Roaming,.config]/app/config2.json",
                )
                == ".config/app/config2.json"
            )
        with patch("mackup_ng.appsdb.platform.system", return_value="Darwin"):
            assert (
                ApplicationsDatabase._resolve_platform_selectors(
                    "[linux:.config/,mac:Library/Application Support,windows:AppData/Roaming,.config]/app/config2.json",
                )
                == "Library/Application Support/app/config2.json"
            )
        with patch("mackup_ng.appsdb.platform.system", return_value="Windows"):
            assert (
                ApplicationsDatabase._resolve_platform_selectors(
                    "[linux:.config/,mac:Library/Application Support,windows:AppData/Roaming,.config]/app/config2.json",
                )
                == "AppData/Roaming/app/config2.json"
            )

    def test_platform_selector_uses_unkeyed_fallback(self):
        """Selector should use the last unkeyed item as fallback."""
        with patch("mackup_ng.appsdb.platform.system", return_value="Linux"):
            assert (
                ApplicationsDatabase._resolve_platform_selectors(
                    "[mac:Library/Application Support/app/mac.conf,windows:AppData/Roaming/app/windows.conf,.config/app/other.conf]",
                )
                == ".config/app/other.conf"
            )

    def test_platform_selector_mapping_uses_fallback_as_backup_path(self):
        """Unkeyed fallback acts as canonical backup path for all platforms."""
        with patch("mackup_ng.appsdb.platform.system", return_value="Darwin"):
            local_path, backup_path = ApplicationsDatabase._resolve_platform_selectors_with_backup(
                "[mac:a/b/c,linux:x/y/z,m/n/o]",
            )
            assert local_path == "a/b/c"
            assert backup_path == "m/n/o"

        with patch("mackup_ng.appsdb.platform.system", return_value="Linux"):
            local_path, backup_path = ApplicationsDatabase._resolve_platform_selectors_with_backup(
                "[mac:a/b/c,linux:x/y/z,m/n/o]",
            )
            assert local_path == "x/y/z"
            assert backup_path == "m/n/o"

        with patch("mackup_ng.appsdb.platform.system", return_value="Linux"):
            local_path, backup_path = ApplicationsDatabase._resolve_platform_selectors_with_backup(
                "[mac:a/b/c,m/n/o]",
            )
            assert local_path == "m/n/o"
            assert backup_path == "m/n/o"

    def test_cross_platform_builtin_variables_expand(self):
        """Generic built-in vars should map to platform-specific directories."""
        with patch("mackup_ng.appsdb.platform.system", return_value="Linux"):
            assert (
                ApplicationsDatabase._expand_builtin_path_vars(
                    "${MACKUP_XDG_CONFIG}/app/config.json",
                )
                == ".config/app/config.json"
            )
            assert (
                ApplicationsDatabase._expand_builtin_path_vars("${MACKUP_XDG_CACHE}/tool/cache.db")
                == ".cache/tool/cache.db"
            )

        with patch("mackup_ng.appsdb.platform.system", return_value="Darwin"):
            assert (
                ApplicationsDatabase._expand_builtin_path_vars(
                    "${MACKUP_XDG_CONFIG}/app/config.json",
                )
                == "Library/Application Support/app/config.json"
            )
            assert (
                ApplicationsDatabase._expand_builtin_path_vars("${MACKUP_XDG_CACHE}/tool/cache.db")
                == "Library/Caches/tool/cache.db"
            )

        with patch("mackup_ng.appsdb.platform.system", return_value="Windows"):
            assert (
                ApplicationsDatabase._expand_builtin_path_vars(
                    "${MACKUP_XDG_CONFIG}/app/config.json",
                )
                == "AppData/Roaming/app/config.json"
            )
            assert (
                ApplicationsDatabase._expand_builtin_path_vars("${MACKUP_XDG_DATA}/tool/data.db")
                == "AppData/Local/tool/data.db"
            )

    def test_cross_platform_builtin_variables_expand_to_linux_for_backup(self):
        """Backup path expansion should use Linux canonical values."""
        with patch("mackup_ng.appsdb.platform.system", return_value="Darwin"):
            assert (
                ApplicationsDatabase._expand_builtin_path_vars(
                    "${MACKUP_XDG_CONFIG}/app/config.json", for_backup=True,
                )
                == ".config/app/config.json"
            )
            assert (
                ApplicationsDatabase._expand_builtin_path_vars(
                    "${MACKUP_XDG_DATA}/app/data.json", for_backup=True,
                )
                == ".local/share/app/data.json"
            )

    def test_applications_database_resolves_selectors_before_expanding_braces(self):
        """Selectors are resolved first, then braces are expanded."""
        temp_home = tempfile.mkdtemp()
        temp_xdg = os.path.join(temp_home, ".config")
        legacy_apps_dir = os.path.join(temp_home, ".mackup", "applications")
        os.makedirs(legacy_apps_dir, exist_ok=True)

        cfg_path = os.path.join(legacy_apps_dir, "platform-selector-test.toml")
        with open(cfg_path, "w") as f:
            f.write(
                "[application]\n"
                'name = "Platform Selector Test"\n'
                "files = [\n"
                '    "[linux:.config/app/{linux.conf,common.conf},mac:Library/Application Support/app/{mac.conf,common.conf},windows:AppData/Roaming/app/{windows.conf,common.conf},.config/app/other.conf]",\n'
                "]\n",
            )

        old_home = os.environ.get("HOME")
        old_xdg = os.environ.get("XDG_CONFIG_HOME")
        try:
            os.environ["HOME"] = temp_home
            os.environ["XDG_CONFIG_HOME"] = temp_xdg

            with patch("mackup_ng.appsdb.platform.system", return_value="Linux"):
                db = ApplicationsDatabase()
                files = db.get_files("platform-selector-test")
                assert ".config/app/linux.conf" in files
                assert ".config/app/common.conf" in files
                assert ".config/app/other.conf" not in files
                assert "Library/Application Support/app/mac.conf" not in files
                mappings = db.get_file_mappings("platform-selector-test")
                assert (".config/app/linux.conf", ".config/app/other.conf") in mappings
                assert (".config/app/common.conf", ".config/app/other.conf") in mappings
        finally:
            if old_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = old_home
            if old_xdg is None:
                os.environ.pop("XDG_CONFIG_HOME", None)
            else:
                os.environ["XDG_CONFIG_HOME"] = old_xdg
            shutil.rmtree(temp_home)

    def test_applications_database_keeps_distinct_local_and_backup_paths(self):
        """Mappings should preserve per-platform local path and canonical backup path."""
        temp_home = tempfile.mkdtemp()
        temp_xdg = os.path.join(temp_home, ".config")
        legacy_apps_dir = os.path.join(temp_home, ".mackup", "applications")
        os.makedirs(legacy_apps_dir, exist_ok=True)

        cfg_path = os.path.join(legacy_apps_dir, "mapping-test.toml")
        with open(cfg_path, "w") as f:
            f.write(
                "[application]\n"
                'name = "Mapping Test"\n'
                "files = [\n"
                '    "[mac:${MACKUP_XDG_CONFIG}/MyApp/config.json,linux:${MACKUP_XDG_CONFIG}/myapp/config.json,${MACKUP_XDG_DATA}/shared/myapp-config.json]",\n'
                "]\n",
            )

        old_home = os.environ.get("HOME")
        old_xdg = os.environ.get("XDG_CONFIG_HOME")
        try:
            os.environ["HOME"] = temp_home
            os.environ["XDG_CONFIG_HOME"] = temp_xdg

            with patch("mackup_ng.appsdb.platform.system", return_value="Linux"):
                db = ApplicationsDatabase()
                assert ".config/myapp/config.json" in db.get_files("mapping-test")
                mappings = db.get_file_mappings("mapping-test")
                assert (
                    ".config/myapp/config.json",
                    ".local/share/shared/myapp-config.json",
                ) in mappings

            with patch("mackup_ng.appsdb.platform.system", return_value="Darwin"):
                db = ApplicationsDatabase()
                assert "Library/Application Support/MyApp/config.json" in db.get_files(
                    "mapping-test",
                )
                mappings = db.get_file_mappings("mapping-test")
                assert (
                    "Library/Application Support/MyApp/config.json",
                    ".local/share/shared/myapp-config.json",
                ) in mappings
        finally:
            if old_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = old_home
            if old_xdg is None:
                os.environ.pop("XDG_CONFIG_HOME", None)
            else:
                os.environ["XDG_CONFIG_HOME"] = old_xdg
            shutil.rmtree(temp_home)

    def test_applications_database_supports_builtin_vars_with_selectors_and_braces(self):
        """Built-in vars should work after selectors and before brace expansion."""
        temp_home = tempfile.mkdtemp()
        temp_xdg = os.path.join(temp_home, ".config")
        legacy_apps_dir = os.path.join(temp_home, ".mackup", "applications")
        os.makedirs(legacy_apps_dir, exist_ok=True)

        cfg_path = os.path.join(legacy_apps_dir, "builtin-vars-test.toml")
        with open(cfg_path, "w") as f:
            f.write(
                "[application]\n"
                'name = "Builtin Vars Test"\n'
                "files = [\n"
                '    "[linux:${MACKUP_XDG_CONFIG}/demo/{a,b}.json,mac:Library/Application Support/demo/{a,b}.json,${MACKUP_XDG_DATA}/demo/fallback.json]",\n'
                "]\n",
            )

        old_home = os.environ.get("HOME")
        old_xdg = os.environ.get("XDG_CONFIG_HOME")
        try:
            os.environ["HOME"] = temp_home
            os.environ["XDG_CONFIG_HOME"] = temp_xdg

            with patch("mackup_ng.appsdb.platform.system", return_value="Linux"):
                db = ApplicationsDatabase()
                files = db.get_files("builtin-vars-test")
                assert ".config/demo/a.json" in files
                assert ".config/demo/b.json" in files
                assert ".local/share/demo/fallback.json" not in files
        finally:
            if old_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = old_home
            if old_xdg is None:
                os.environ.pop("XDG_CONFIG_HOME", None)
            else:
                os.environ["XDG_CONFIG_HOME"] = old_xdg
            shutil.rmtree(temp_home)

    def test_applications_database_supports_cross_platform_builtin_vars(self):
        """Cross-platform vars should resolve without requiring [] selectors."""
        temp_home = tempfile.mkdtemp()
        temp_xdg = os.path.join(temp_home, ".config")
        legacy_apps_dir = os.path.join(temp_home, ".mackup", "applications")
        os.makedirs(legacy_apps_dir, exist_ok=True)

        cfg_path = os.path.join(legacy_apps_dir, "cross-platform-vars-test.toml")
        with open(cfg_path, "w") as f:
            f.write(
                "[application]\n"
                'name = "Cross Platform Vars Test"\n'
                "files = [\n"
                '    "${MACKUP_XDG_CONFIG}/demo/{a,b}.json",\n'
                '    "${MACKUP_XDG_CACHE}/demo/cache.db",\n'
                "]\n",
            )

        old_home = os.environ.get("HOME")
        old_xdg = os.environ.get("XDG_CONFIG_HOME")
        try:
            os.environ["HOME"] = temp_home
            os.environ["XDG_CONFIG_HOME"] = temp_xdg

            with patch("mackup_ng.appsdb.platform.system", return_value="Darwin"):
                db = ApplicationsDatabase()
                files = db.get_files("cross-platform-vars-test")
                assert "Library/Application Support/demo/a.json" in files
                assert "Library/Application Support/demo/b.json" in files
                assert "Library/Caches/demo/cache.db" in files
                assert ".config/demo/a.json" not in files
        finally:
            if old_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = old_home
            if old_xdg is None:
                os.environ.pop("XDG_CONFIG_HOME", None)
            else:
                os.environ["XDG_CONFIG_HOME"] = old_xdg
            shutil.rmtree(temp_home)


if __name__ == "__main__":
    unittest.main()
