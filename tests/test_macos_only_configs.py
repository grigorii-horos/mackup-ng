"""Shipped configs must not offer ~/Library paths to machines that have none.

`~/Library` exists only on macOS. A config that syncs nothing else has no
business being considered on Linux or Windows, so it declares
`[when] os = "macos"` and is skipped whole. A config that syncs both kinds of
path cannot say that — gating the file would take its Linux paths down with
it — so those are split into two configs instead, one per platform.

Both rules are enforced here so that a config added later cannot quietly
reintroduce either shape.
"""

import pathlib
import tomllib

import pytest

APPS_DIR = pathlib.Path(__file__).resolve().parent.parent / "src" / "mackup_ng" / "applications"

LIBRARY_PREFIX = "Library/"


def _config_paths(data: dict) -> list[str]:
    """Every local path a config claims: its `files` plus both sides of any
    `mapped_files` entry."""
    found = [p for p in data.get("files", []) or [] if isinstance(p, str)]
    mapped = data.get("mapped_files") or {}
    if isinstance(mapped, dict):
        for source, dests in mapped.items():
            if isinstance(source, str):
                found.append(source)
            found.extend(
                dest
                for dest in (dests if isinstance(dests, list) else [dests])
                if isinstance(dest, str)
            )
    return found


def _all_configs():
    for path in sorted(APPS_DIR.glob("*.toml")):
        with path.open("rb") as handle:
            yield path, tomllib.load(handle)


def _library_split(data: dict) -> tuple[list[str], list[str]]:
    paths = _config_paths(data)
    library = [p for p in paths if p.startswith(LIBRARY_PREFIX)]
    other = [p for p in paths if not p.startswith(LIBRARY_PREFIX)]
    return library, other


def test_at_least_one_library_config_exists():
    """Guards the two tests below against silently passing on an empty set."""
    with_library = [
        path.name for path, data in _all_configs() if _library_split(data)[0]
    ]

    assert len(with_library) > 100, f"only {len(with_library)} found"


def test_library_only_configs_are_gated_to_macos():
    offenders = []
    for path, data in _all_configs():
        library, other = _library_split(data)
        if library and not other and data.get("when", {}).get("os") != "macos":
            offenders.append(path.name)

    assert offenders == [], (
        f"{len(offenders)} config(s) sync only ~/Library but are still"
        f" considered on every platform: {offenders[:10]}"
    )


def test_no_config_mixes_library_with_other_paths():
    offenders = []
    for path, data in _all_configs():
        library, other = _library_split(data)
        if library and other:
            offenders.append(path.name)

    assert offenders == [], (
        f"{len(offenders)} config(s) mix ~/Library with paths for other"
        " platforms; a config-level [when] cannot express that, so split them"
        f" into <app>.toml and <app>-macos.toml: {offenders[:10]}"
    )


@pytest.mark.parametrize("suffix", ["-macos"])
def test_split_macos_configs_are_gated(suffix):
    """The macOS half of a split pair must carry the gate."""
    offenders = [
        path.name
        for path, data in _all_configs()
        if path.stem.endswith(suffix) and data.get("when", {}).get("os") != "macos"
    ]

    assert offenders == [], f"split macOS configs missing the gate: {offenders[:10]}"
