"""Config rejects the legacy layout and bad values loudly, not silently."""

import os

import pytest

from mackup_ng.config import Config, ConfigError


def _xdg_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME"):
        monkeypatch.delenv(var, raising=False)
    config_dir = tmp_path / ".config" / "mackup"
    config_dir.mkdir(parents=True)
    # Several tests below write a config with no [storage] table, so the
    # engine defaults to dropbox and _parse_path resolves a real
    # ~/.dropbox/host.db. Without one there, Config() exits with "Unable to
    # find your Dropbox install" before it ever reaches the validation or
    # warning behaviour under test. tests/fixtures/.dropbox/host.db carries
    # the same fake entry; mirror it under this test's own $HOME.
    dropbox_dir = tmp_path / ".dropbox"
    dropbox_dir.mkdir()
    (dropbox_dir / "host.db").write_text(
        "0000000000000000000000000000000000000000\n"
        "L2hvbWUvc29tZV91c2VyL0Ryb3Bib3g=\n",
    )
    return config_dir


def test_legacy_config_file_is_rejected(tmp_path, monkeypatch):
    _xdg_home(tmp_path, monkeypatch)
    (tmp_path / ".mackup.cfg").write_text("[storage]\nengine = dropbox\n")

    with pytest.raises(SystemExit) as excinfo:
        Config()

    assert "config.toml" in str(excinfo.value)


def test_legacy_home_directory_is_rejected(tmp_path, monkeypatch):
    _xdg_home(tmp_path, monkeypatch)
    (tmp_path / ".mackup").mkdir()

    with pytest.raises(SystemExit) as excinfo:
        Config()

    assert "config.toml" in str(excinfo.value)


def test_malformed_toml_names_the_file(tmp_path, monkeypatch):
    config_dir = _xdg_home(tmp_path, monkeypatch)
    (config_dir / "config.toml").write_text("[storage\nengine = ")

    with pytest.raises(SystemExit) as excinfo:
        Config()

    assert "config.toml" in str(excinfo.value)


def test_ignore_must_be_a_list(tmp_path, monkeypatch):
    config_dir = _xdg_home(tmp_path, monkeypatch)
    (config_dir / "config.toml").write_text('[applications]\nignore = "ssh"\n')

    with pytest.raises(ConfigError, match=r"applications\.ignore"):
        Config()


def test_unknown_table_warns_but_parses(tmp_path, monkeypatch, capsys):
    config_dir = _xdg_home(tmp_path, monkeypatch)
    (config_dir / "config.toml").write_text(
        '[colors]\nfilename_path_separator = "1;32"\n'
        '\n[applications]\nignore = ["ssh"]\n',
    )

    cfg = Config()

    assert cfg.apps_to_ignore == {"ssh"}
    assert "colors" in capsys.readouterr().out


def test_unknown_key_warns(tmp_path, monkeypatch, capsys):
    config_dir = _xdg_home(tmp_path, monkeypatch)
    (config_dir / "config.toml").write_text('[applications]\nignor = ["ssh"]\n')

    Config()

    assert (
        "unknown key(s) in [applications]: ignor" in capsys.readouterr().out
    )


def test_same_config_file_warns_only_once_across_instances(
    tmp_path, monkeypatch, capsys,
):
    """hooks.backup_dir() builds its own Config() per action block, reading
    the same file as the primary flow's instance — the warning must not
    repeat for repeat reads of the *same* resolved path."""
    config_dir = _xdg_home(tmp_path, monkeypatch)
    (config_dir / "config.toml").write_text(
        '[colors]\nfoo = "bar"\n\n[applications]\nignore = ["ssh"]\n',
    )

    Config()
    Config()
    Config()

    assert capsys.readouterr().out.count("unknown config table [colors]") == 1


def test_different_config_files_each_warn_independently(
    tmp_path, monkeypatch, capsys,
):
    """`mackup apply --config-file other.toml` reads two different files in
    one run: the primary flow honours --config-file, but hooks.backup_dir()
    always builds Config() with no filename, i.e. the default location. Both
    files' unknown keys must be reported — keying the once-only warning by a
    bare per-process flag would silently swallow the second file's warning
    once the first file had already tripped it.
    """
    config_dir = _xdg_home(tmp_path, monkeypatch)
    (config_dir / "config.toml").write_text(
        '[colors]\nfoo = "bar"\n\n[applications]\nignore = ["ssh"]\n',
    )
    other = tmp_path / "other.toml"
    other.write_text(
        '[weirdtable]\nfoo = "bar"\n\n[applications]\nignore = ["git"]\n',
    )

    Config()  # default location, as hooks.backup_dir() would build it
    Config(str(other))  # --config-file, as the primary flow would build it

    output = capsys.readouterr().out
    assert "unknown config table [colors]" in output
    assert "unknown config table [weirdtable]" in output


def test_storage_directory_inside_a_managed_dir_is_rejected(tmp_path, monkeypatch):
    config_dir = _xdg_home(tmp_path, monkeypatch)
    (config_dir / "config.toml").write_text(
        '[storage]\nengine = "file_system"\n'
        'path = ".config/mackup"\ndirectory = "applications"\n',
    )

    with pytest.raises(ConfigError, match="manages"):
        Config()


def test_storage_table_must_be_a_table(tmp_path, monkeypatch):
    config_dir = _xdg_home(tmp_path, monkeypatch)
    (config_dir / "config.toml").write_text('storage = "dropbox"\n')

    with pytest.raises(ConfigError, match="storage"):
        Config()


def test_storage_engine_must_be_a_string(tmp_path, monkeypatch):
    config_dir = _xdg_home(tmp_path, monkeypatch)
    (config_dir / "config.toml").write_text("[storage]\nengine = 42\n")

    with pytest.raises(ConfigError, match=r"storage\.engine"):
        Config()


def test_storage_path_must_be_a_string(tmp_path, monkeypatch):
    config_dir = _xdg_home(tmp_path, monkeypatch)
    (config_dir / "config.toml").write_text(
        '[storage]\nengine = "file_system"\npath = 42\n',
    )

    with pytest.raises(ConfigError, match=r"storage\.path"):
        Config()


def test_storage_directory_must_be_a_string(tmp_path, monkeypatch):
    config_dir = _xdg_home(tmp_path, monkeypatch)
    (config_dir / "config.toml").write_text(
        '[storage]\nengine = "file_system"\npath = "somewhere"\ndirectory = 42\n',
    )

    with pytest.raises(ConfigError, match=r"storage\.directory"):
        Config()


def test_unreadable_config_file_names_the_file(tmp_path, monkeypatch):
    if os.geteuid() == 0:
        pytest.skip("root bypasses file permissions, chmod 000 has no effect")

    config_dir = _xdg_home(tmp_path, monkeypatch)
    config_path = config_dir / "config.toml"
    config_path.write_text('[storage]\nengine = "dropbox"\n')
    config_path.chmod(0o000)

    try:
        with pytest.raises(SystemExit) as excinfo:
            Config()
    finally:
        config_path.chmod(0o644)

    assert "config.toml" in str(excinfo.value)
