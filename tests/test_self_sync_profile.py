"""The Mackup profile syncs its own config and data, never its state."""

import os
import tomllib


def _profile() -> dict:
    here = os.path.dirname(os.path.realpath(__file__))
    path = os.path.join(
        here, "..", "src", "mackup_ng", "applications", "mackup.toml",
    )
    with open(path, "rb") as handle:
        return tomllib.load(handle)


def test_profile_syncs_config_and_data():
    files = _profile()["files"]

    assert ".config/mackup" in files
    assert ".local/share/mackup" in files


def test_profile_never_syncs_machine_local_state():
    """Syncing marker flags would carry the `backup` role to another machine
    and invert the direction of its next sync."""
    files = _profile()["files"]

    assert not any(f.startswith(".local/state") for f in files)
    assert ".mackup" not in files
    assert ".mackup.cfg" not in files
