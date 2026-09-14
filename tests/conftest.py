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
    """Write a file_system-engine TOML mackup config to `path`.

    Creates `path`'s parent directory if needed. `sync` and `ignore` are
    iterables of app ids written as the `[applications]` arrays.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    sync_entries = ", ".join(f'"{app}"' for app in sync)
    lines = [
        "[storage]\n",
        'engine = "file_system"\n',
        f'path = "{storage_path}"\n',
        f'directory = "{directory}"\n',
        "\n",
        "[applications]\n",
        f"sync = [{sync_entries}]\n",
    ]
    if ignore:
        ignore_entries = ", ".join(f'"{app}"' for app in ignore)
        lines.append(f"ignore = [{ignore_entries}]\n")
    with open(path, "w") as handle:
        handle.writelines(lines)
