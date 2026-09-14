"""Shared test helpers."""

import os

import pytest

from mackup_ng import config as _config


@pytest.fixture(autouse=True)
def _reset_unknown_key_warning():
    """Config._warn_on_unknown_keys() fires at most once per resolved path.

    Without this, whichever test happens to run first and trip that warning
    for a given config path would permanently silence it for every later
    test that resolves to the same path — including the ones in
    tests/test_config_errors.py that assert on its text.
    """
    _config._reset_unknown_key_warning()
    yield
    _config._reset_unknown_key_warning()


def write_config(path, *, storage_path, directory="Mackup", sync=(), ignore=()):
    """Write a TOML mackup config to `path`.

    `storage_path` and `directory` are joined into the single
    `storage.backup_dir` the config now carries; they stay separate arguments
    because every caller builds its backup folder that way and asserts
    against the same two pieces.

    Creates `path`'s parent directory if needed. `sync` and `ignore` are
    iterables of app ids written as the `[applications]` arrays.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    backup_dir = os.path.join(storage_path, directory)
    sync_entries = ", ".join(f'"{app}"' for app in sync)
    lines = [
        "[storage]\n",
        f'backup_dir = "{backup_dir}"\n',
        "\n",
        "[applications]\n",
        f"sync = [{sync_entries}]\n",
    ]
    if ignore:
        ignore_entries = ", ".join(f'"{app}"' for app in ignore)
        lines.append(f"ignore = [{ignore_entries}]\n")
    with open(path, "w") as handle:
        handle.writelines(lines)
